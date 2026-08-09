from __future__ import annotations

from datetime import datetime
from typing import Any

from .errors import AgentInfraError
from .runtime import RunResult


class OpenTelemetrySink:
    """Export completed run lineage using the application's configured OTel provider."""

    def __init__(self, tracer_name: str = "agent_infra") -> None:
        try:
            from opentelemetry import trace
        except ImportError as exc:
            raise AgentInfraError(
                "OpenTelemetry export requires the optional dependency: pip install 'real-agentic-workflows[otel]'"
            ) from exc
        self.trace = trace
        self.tracer = trace.get_tracer(tracer_name)

    @staticmethod
    def _nanoseconds(value: str) -> int:
        return int(datetime.fromisoformat(value).timestamp() * 1_000_000_000)

    @staticmethod
    def _attributes(value: dict[str, Any]) -> dict[str, Any]:
        allowed = (str, bool, int, float)
        return {key: item for key, item in value.items() if isinstance(item, allowed) and item is not None}

    def record_run(self, result: RunResult) -> None:
        workflow_span = self.tracer.start_span(
            f"workflow {result.workflow_name}",
            start_time=self._nanoseconds(result.started_at),
            attributes={
                "gen_ai.operation.name": "execute_workflow",
                "gen_ai.workflow.name": result.workflow_name,
                "agent_infra.workflow.version": result.workflow_version,
                "agent_infra.plan.digest": result.plan_digest,
                "agent_infra.run.id": result.run_id,
            },
        )
        if result.status == "failed":
            workflow_span.set_status(self.trace.Status(self.trace.StatusCode.ERROR, result.error))
        workflow_context = self.trace.set_span_in_context(workflow_span)
        for node in result.node_runs:
            span = self.tracer.start_span(
                f"node {node.node_id}",
                context=workflow_context,
                start_time=self._nanoseconds(node.started_at),
                attributes=self._attributes(
                    {
                        "agent_infra.node.id": node.node_id,
                        "agent_infra.node.type": node.node_type,
                        "agent_infra.node.hash": node.node_hash,
                        "agent_infra.node.attempt": node.attempt,
                        **node.attributes,
                    }
                ),
            )
            if node.status == "failed":
                span.set_status(self.trace.Status(self.trace.StatusCode.ERROR, node.error))
            span.end(end_time=self._nanoseconds(node.ended_at))
        workflow_span.end(end_time=self._nanoseconds(result.ended_at))
