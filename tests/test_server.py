from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
from helpers import workflow

from agent_infra.compiler import compile_workflow
from agent_infra.runtime import Runtime
from agent_infra.server import AgentServer
from agent_infra.store import Store


def request(
    url: str,
    value: dict[str, object] | None = None,
    key: str | None = None,
    idempotency_key: str | None = None,
):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return urllib.request.urlopen(
        urllib.request.Request(
            url,
            data=json.dumps(value).encode() if value is not None else None,
            headers=headers,
            method="POST" if value is not None else "GET",
        )
    )


def test_server_executes_deployment_and_reads_trace(tmp_path) -> None:
    store = Store(tmp_path / "state.db")
    plan = compile_workflow(workflow())
    store.deploy(plan, environment="prod", updated_at="2026-01-01T00:00:00Z")
    server = AgentServer(store, Runtime(trace_sink=store, plan_loader=store.load_plan), port=0, api_key="secret")
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            request(server.url + "/v1/runs", {"workflow": "support_flow", "input": {"message": "hi"}})
        assert unauthorized.value.code == 401
        with request(
            server.url + "/v1/runs",
            {"workflow": "support_flow", "input": {"message": "hi"}},
            "secret",
        ) as response:
            assert response.status == 201
            run = json.load(response)
        assert run["output"] == {"message": "Received: hi"}
        with request(server.url + "/v1/runs/" + run["run_id"], key="secret") as response:
            trace = json.load(response)
        assert trace["plan_digest"] == plan.digest

        body = {"workflow": "support_flow", "input": {"message": "once"}}
        with request(server.url + "/v1/runs", body, "secret", "request-1") as response:
            first = json.load(response)
        with request(server.url + "/v1/runs", body, "secret", "request-1") as response:
            replay = json.load(response)
        assert replay["run_id"] == first["run_id"]
        assert replay["idempotent_replay"] is True
        with pytest.raises(urllib.error.HTTPError) as conflict:
            request(
                server.url + "/v1/runs",
                {"workflow": "support_flow", "input": {"message": "different"}},
                "secret",
                "request-1",
            )
        assert conflict.value.code == 409
    finally:
        server.close()
        thread.join(timeout=2)
