from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_infra.adapters import HTTPJSONTool, MCPStreamableHTTPTool, OpenAICompatibleProvider
from agent_infra.errors import ExecutionFailed


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        return

    def do_POST(self) -> None:
        value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/redirect":
            self.send_response(307)
            self.send_header("Location", f"http://{self.headers['Host']}/tool")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/mcp" and value["method"] == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/mcp" and value["method"] == "initialize":
            result = {
                "jsonrpc": "2.0",
                "id": value["id"],
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "test", "version": "1"},
                },
            }
        elif self.path == "/mcp" and value["method"] == "tools/call":
            result = {
                "jsonrpc": "2.0",
                "id": value["id"],
                "result": {
                    "content": [],
                    "structuredContent": {"tool": value["params"]["name"], "arguments": value["params"]["arguments"]},
                },
            }
        elif self.path == "/tool":
            result = {"received": value}
        else:
            result = {
                "model": value["model"] + "-resolved",
                "choices": [{"message": {"content": value["messages"][-1]["content"].upper()}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if self.path == "/mcp":
            self.send_header("Mcp-Session-Id", "session-1")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_tool_and_provider(http_server: str) -> None:
    tool = HTTPJSONTool(http_server + "/tool", allowed_hosts=("127.0.0.1",))
    assert tool({"x": 1}) == {"received": {"x": 1}}
    provider = OpenAICompatibleProvider(http_server, allowed_hosts=("127.0.0.1",))
    response = provider({"model": "test", "prompt": "hello", "system": None, "parameters": {}})
    assert response == {
        "output": "HELLO",
        "input_tokens": 3,
        "output_tokens": 2,
        "response_model": "test-resolved",
    }


def test_http_adapter_enforces_allowlist(http_server: str) -> None:
    with pytest.raises(ExecutionFailed, match="allowlist"):
        HTTPJSONTool(http_server + "/tool", allowed_hosts=("example.com",))({})
    with pytest.raises(ExecutionFailed, match="non-empty host allowlist"):
        HTTPJSONTool(http_server + "/tool")({})


def test_http_adapter_does_not_follow_redirects(http_server: str) -> None:
    with pytest.raises(ExecutionFailed, match="HTTP 307"):
        HTTPJSONTool(http_server + "/redirect", allowed_hosts=("127.0.0.1",))({})


def test_mcp_streamable_http_lifecycle_and_structured_result(http_server: str) -> None:
    tool = MCPStreamableHTTPTool(
        http_server + "/mcp",
        "lookup",
        allowed_hosts=("127.0.0.1",),
    )
    assert tool({"id": "C-1"}) == {"tool": "lookup", "arguments": {"id": "C-1"}}
