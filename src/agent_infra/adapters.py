from __future__ import annotations

import json
import os
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

from .errors import ExecutionFailed

MAX_RESPONSE_BYTES = 10_000_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not let an allowlisted endpoint redirect a request to another host."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _request_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
    allowed_hosts: tuple[str, ...] = (),
    empty_ok: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ExecutionFailed("adapter URL must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ExecutionFailed("adapter URL must not contain credentials")
    if not allowed_hosts:
        raise ExecutionFailed("adapter requests require a non-empty host allowlist")
    normalized_hosts = {item.casefold() for item in allowed_hosts}
    if parsed.hostname.casefold() not in normalized_hosts:
        raise ExecutionFailed(f"adapter host {parsed.hostname!r} is not in the allowlist")
    if timeout <= 0:
        raise ExecutionFailed("adapter timeout must be positive")
    request_headers = {"Accept": "application/json", "Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers=request_headers,
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        with opener.open(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            raw_body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw_body) > MAX_RESPONSE_BYTES:
                raise ExecutionFailed(f"adapter response exceeds {MAX_RESPONSE_BYTES} bytes")
            body = raw_body.decode("utf-8")
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        raise ExecutionFailed(f"adapter HTTP {exc.code} from {url}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ExecutionFailed(f"adapter request to {url} failed: {exc}") from exc
    if not body.strip() and empty_ok:
        return {}, response_headers
    if "text/event-stream" in content_type:
        data_lines = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
        if not data_lines:
            raise ExecutionFailed("adapter returned an empty event stream")
        body = data_lines[-1]
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ExecutionFailed(f"adapter returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ExecutionFailed("adapter response must be a JSON object")
    return value, response_headers


@dataclass
class HTTPJSONTool:
    """A registry-compatible JSON-over-HTTP tool with an explicit host allowlist."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30
    allowed_hosts: tuple[str, ...] = ()

    def __call__(self, arguments: dict[str, Any]) -> Any:
        response, _ = _request_json(
            self.url,
            arguments,
            headers=self.headers,
            timeout=self.timeout,
            allowed_hosts=self.allowed_hosts,
        )
        return response


@dataclass
class OpenAICompatibleProvider:
    """Provider adapter for the widely implemented OpenAI Chat Completions wire format."""

    base_url: str
    api_key: str | None = None
    api_key_env: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 60
    allowed_hosts: tuple[str, ...] = ()

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        key = self.api_key or (os.environ.get(self.api_key_env) if self.api_key_env else None)
        if self.api_key_env and not key:
            raise ExecutionFailed(f"model API key environment variable {self.api_key_env!r} is empty or missing")
        headers = dict(self.headers)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        messages = []
        if request.get("system") is not None:
            messages.append({"role": "system", "content": request["system"]})
        messages.append({"role": "user", "content": request["prompt"]})
        payload = {"model": request["model"], "messages": messages, **request.get("parameters", {})}
        endpoint = self.base_url.rstrip("/") + "/chat/completions"
        response, _ = _request_json(
            endpoint,
            payload,
            headers=headers,
            timeout=self.timeout,
            allowed_hosts=self.allowed_hosts,
        )
        try:
            output = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ExecutionFailed("model response does not contain choices[0].message.content") from exc
        usage = response.get("usage", {})
        return {
            "output": output,
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "response_model": response.get("model"),
        }


@dataclass
class MCPStreamableHTTPTool:
    """A minimal MCP 2025-06-18 Streamable HTTP client for a named remote tool."""

    url: str
    tool_name: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30
    allowed_hosts: tuple[str, ...] = ()
    protocol_version: str = "2025-06-18"
    _session_id: str | None = field(default=None, init=False)
    _initialized: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _rpc(self, method: str, params: dict[str, Any] | None, *, notification: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        request_id = None
        if not notification:
            request_id = uuid.uuid4().hex
            payload["id"] = request_id
        if params is not None:
            payload["params"] = params
        headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol_version,
            **self.headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        response, response_headers = _request_json(
            self.url,
            payload,
            headers=headers,
            timeout=self.timeout,
            allowed_hosts=self.allowed_hosts,
            empty_ok=notification,
        )
        if response_headers.get("mcp-session-id"):
            self._session_id = response_headers["mcp-session-id"]
        if not notification and (response.get("jsonrpc") != "2.0" or response.get("id") != request_id):
            raise ExecutionFailed(f"MCP {method} returned a mismatched JSON-RPC response")
        if "error" in response:
            error = response["error"]
            raise ExecutionFailed(f"MCP {method} failed: {error.get('message', error)}")
        return response

    def _initialize(self) -> None:
        if self._initialized:
            return
        response = self._rpc(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "real-agentic-workflows", "version": "0.1.0"},
            },
        )
        negotiated = response.get("result", {}).get("protocolVersion")
        if negotiated and negotiated != self.protocol_version:
            raise ExecutionFailed(
                f"MCP server negotiated unsupported protocol {negotiated!r}; expected {self.protocol_version!r}"
            )
        self._rpc("notifications/initialized", None, notification=True)
        self._initialized = True

    def __call__(self, arguments: dict[str, Any]) -> Any:
        with self._lock:
            self._initialize()
            response = self._rpc("tools/call", {"name": self.tool_name, "arguments": arguments})
        result = response.get("result", {})
        if result.get("isError"):
            raise ExecutionFailed(f"MCP tool {self.tool_name!r} reported an error: {result.get('content')}")
        if "structuredContent" in result:
            return result["structuredContent"]
        content = result.get("content", [])
        if len(content) == 1 and content[0].get("type") == "text":
            return content[0].get("text")
        return content
