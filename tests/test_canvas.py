from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
from helpers import workflow

from agent_infra.canvas import CanvasServer
from agent_infra.codec import pretty_json


def test_canvas_reads_and_conditionally_updates_same_source(tmp_path) -> None:
    source = tmp_path / "workflow.json"
    source.write_text(pretty_json(workflow().to_dict()), encoding="utf-8")
    server = CanvasServer(source, port=0)
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(server.url + "api/workflow") as response:
            value = json.load(response)
            etag = response.headers["ETag"]
        value["description"] = "edited through Canvas"
        request = urllib.request.Request(
            server.url + "api/workflow",
            data=json.dumps(value).encode(),
            headers={"Content-Type": "application/json", "If-Match": etag},
            method="PUT",
        )
        with urllib.request.urlopen(request) as response:
            assert json.load(response)["ok"] is True
        assert json.loads(source.read_text())["description"] == "edited through Canvas"
        with pytest.raises(urllib.error.HTTPError) as stale:
            urllib.request.urlopen(request)
        assert stale.value.code == 412
    finally:
        server.close()
        thread.join(timeout=2)
