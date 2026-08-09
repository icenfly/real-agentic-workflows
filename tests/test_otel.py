from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

from helpers import workflow

from agent_infra.compiler import compile_workflow
from agent_infra.otel import OpenTelemetrySink
from agent_infra.runtime import Runtime


class FakeSpan:
    def __init__(self, name: str, **options: Any) -> None:
        self.name = name
        self.options = options
        self.status = None
        self.ended_at = None

    def set_status(self, status: Any) -> None:
        self.status = status

    def end(self, *, end_time: int) -> None:
        self.ended_at = end_time


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []

    def start_span(self, name: str, **options: Any) -> FakeSpan:
        span = FakeSpan(name, **options)
        self.spans.append(span)
        return span


def test_otel_sink_exports_workflow_and_node_lineage(monkeypatch) -> None:
    tracer = FakeTracer()
    fake_trace = SimpleNamespace(
        get_tracer=lambda _: tracer,
        set_span_in_context=lambda span: span,
        Status=lambda code, message=None: (code, message),
        StatusCode=SimpleNamespace(ERROR="error"),
    )
    package = ModuleType("opentelemetry")
    package.trace = fake_trace
    monkeypatch.setitem(sys.modules, "opentelemetry", package)

    result = Runtime().run(compile_workflow(workflow()), {"message": "observable"})
    OpenTelemetrySink().record_run(result)

    assert [span.name for span in tracer.spans] == ["workflow support_flow", "node render", "node result"]
    workflow_attributes = tracer.spans[0].options["attributes"]
    assert workflow_attributes["agent_infra.plan.digest"] == result.plan_digest
    assert tracer.spans[1].options["context"] is tracer.spans[0]
    assert tracer.spans[1].options["attributes"]["agent_infra.node.hash"] == result.node_runs[0].node_hash
    assert all(span.ended_at is not None for span in tracer.spans)
