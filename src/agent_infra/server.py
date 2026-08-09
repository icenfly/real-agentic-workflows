from __future__ import annotations

import hashlib
import hmac
import json
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from .codec import canonical_json, pretty_json
from .errors import AgentInfraError
from .experiments import ExperimentManager
from .runtime import Runtime
from .store import Store


class AgentServer:
    """Small deployable HTTP data plane backed by the same Store and Runtime as the CLI."""

    def __init__(
        self,
        store: Store,
        runtime: Runtime,
        *,
        host: str = "127.0.0.1",
        port: int = 8080,
        api_key: str | None = None,
        max_concurrency: int = 32,
        max_body_bytes: int = 2_000_000,
    ) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"} and not api_key:
            raise AgentInfraError("binding beyond loopback requires an API key")
        if max_concurrency < 1:
            raise AgentInfraError("max_concurrency must be positive")
        self.store = store
        self.runtime = runtime
        self.manager = ExperimentManager(store, actor="http-api")
        self.host = host
        self.port = port
        self.api_key = api_key
        self.max_body_bytes = max_body_bytes
        self.slots = threading.BoundedSemaphore(max_concurrency)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "REAL/0.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send(self, status: int, value: Any) -> None:
                body = pretty_json(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                if outer.api_key is None:
                    return True
                authorization = self.headers.get("Authorization", "")
                expected = f"Bearer {outer.api_key}"
                return hmac.compare_digest(authorization, expected)

            def _body(self) -> dict[str, Any]:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise AgentInfraError("invalid Content-Length") from exc
                if length <= 0 or length > outer.max_body_bytes:
                    raise AgentInfraError("request body is empty or too large")
                try:
                    value = json.loads(self.rfile.read(length))
                except json.JSONDecodeError as exc:
                    raise AgentInfraError(f"invalid JSON body: {exc}") from exc
                if not isinstance(value, dict):
                    raise AgentInfraError("request body must be a JSON object")
                return value

            def _guard(self) -> bool:
                if not self._authorized():
                    self._send(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                    return False
                if not outer.slots.acquire(blocking=False):
                    self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "server at concurrency limit"})
                    return False
                return True

            def do_GET(self) -> None:
                if self.path == "/healthz":
                    self._send(HTTPStatus.OK, {"ok": True})
                    return
                if not self._guard():
                    return
                try:
                    path = urlparse(self.path).path
                    if path.startswith("/v1/runs/"):
                        self._send(HTTPStatus.OK, outer.store.get_run(unquote(path.removeprefix("/v1/runs/"))))
                    elif path.startswith("/v1/experiments/"):
                        self._send(HTTPStatus.OK, outer.manager.status(unquote(path.removeprefix("/v1/experiments/"))))
                    else:
                        self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                except AgentInfraError as exc:
                    self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
                except Exception:
                    self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "internal server error"})
                finally:
                    outer.slots.release()

            def do_POST(self) -> None:
                if not self._guard():
                    return
                try:
                    path = urlparse(self.path).path
                    body = self._body()
                    if path == "/v1/runs":
                        self._run(body)
                    elif path == "/v1/outcomes":
                        outcome_id = outer.manager.outcome(
                            body.get("assignment_id", ""),
                            metric=body.get("metric", ""),
                            value=float(body["value"]),
                            metadata=body.get("metadata", {}),
                            idempotency_key=body.get("idempotency_key"),
                        )
                        self._send(HTTPStatus.CREATED, {"ok": True, "outcome_id": outcome_id})
                    else:
                        self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                except (AgentInfraError, KeyError, TypeError, ValueError) as exc:
                    self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                except Exception:
                    self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "internal server error"})
                finally:
                    outer.slots.release()

            def _run(self, body: dict[str, Any]) -> None:
                idempotency_key = self.headers.get("Idempotency-Key")
                claim = None
                if idempotency_key:
                    claim = outer.store.claim_idempotency(
                        idempotency_key,
                        hashlib.sha256(canonical_json(body).encode()).hexdigest(),
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                    if claim["status"] == "conflict":
                        self._send(
                            HTTPStatus.CONFLICT,
                            {"ok": False, "error": "Idempotency-Key was reused with a different request"},
                        )
                        return
                    if claim["status"] == "pending":
                        self._send(
                            HTTPStatus.CONFLICT,
                            {"ok": False, "error": "request with this Idempotency-Key is still running"},
                        )
                        return
                    if claim["status"] == "completed":
                        replay = outer.store.get_run(claim["run_id"])
                        replay["idempotent_replay"] = True
                        replay_status = (
                            HTTPStatus.OK if replay["status"] == "succeeded" else HTTPStatus.INTERNAL_SERVER_ERROR
                        )
                        self._send(replay_status, replay)
                        return
                input_value = body.get("input")
                if not isinstance(input_value, dict):
                    if idempotency_key:
                        outer.store.release_idempotency(idempotency_key)
                    raise AgentInfraError("input must be a JSON object")
                assignment = None
                try:
                    if body.get("experiment"):
                        experiment = body["experiment"]
                        if not isinstance(experiment, dict):
                            raise AgentInfraError("experiment must be an object")
                        assignment = outer.manager.assign(
                            experiment.get("name", ""),
                            unit_name=experiment.get("unit_name", ""),
                            unit_value=str(experiment.get("unit_value", "")),
                        )
                        plan = outer.store.load_plan(assignment.plan_digest)
                    elif body.get("plan_digest"):
                        plan = outer.store.load_plan(body["plan_digest"])
                    else:
                        workflow_name = body.get("workflow")
                        if not workflow_name:
                            raise AgentInfraError("workflow, plan_digest, or experiment is required")
                        deployment = outer.manager.deployment(workflow_name, body.get("environment", "prod"))
                        plan = outer.store.load_plan(deployment["plan_digest"])
                    result = outer.runtime.run(
                        plan,
                        input_value,
                        experiment=assignment.lineage() if assignment else None,
                    )
                except Exception:
                    if idempotency_key:
                        outer.store.release_idempotency(idempotency_key)
                    raise
                if assignment:
                    outer.manager.expose(assignment, result.run_id)
                    outer.manager.record_run_metrics(assignment, result)
                if idempotency_key:
                    outer.store.finish_idempotency(idempotency_key, result.run_id)
                self._send(
                    HTTPStatus.CREATED if result.status == "succeeded" else HTTPStatus.INTERNAL_SERVER_ERROR,
                    result.to_dict(),
                )

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.port = self.httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def serve(self) -> None:
        self.httpd.serve_forever()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
