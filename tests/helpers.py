from __future__ import annotations

from agent_infra.model import WorkflowSpec


def workflow(version: str = "1.0.0", prefix: str = "Received") -> WorkflowSpec:
    return WorkflowSpec.from_dict(
        {
            "spec_version": "0.1",
            "name": "support_flow",
            "version": version,
            "input_schema": {
                "type": "object",
                "required": ["message"],
                "properties": {"message": {"type": "string"}},
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "required": ["message"],
                "properties": {"message": {"type": "string"}},
            },
            "entry": "render",
            "nodes": [
                {
                    "id": "render",
                    "type": "template",
                    "config": {"template": f"{prefix}: ${{$.input.message}}"},
                },
                {"id": "result", "type": "output", "config": {"value": {"message": "${$.nodes.render}"}}},
            ],
            "edges": [{"source": "render", "target": "result"}],
        }
    )


def branching_workflow() -> WorkflowSpec:
    return WorkflowSpec.from_dict(
        {
            "spec_version": "0.1",
            "name": "route_flow",
            "version": "1.0.0",
            "entry": "route",
            "nodes": [
                {"id": "route", "type": "branch", "config": {"value": "${$.input.kind}"}},
                {"id": "a", "type": "constant", "config": {"value": "A"}},
                {"id": "b", "type": "constant", "config": {"value": "B"}},
                {"id": "result_a", "type": "output", "config": {"value": "${$.nodes.a}"}},
                {"id": "result_b", "type": "output", "config": {"value": "${$.nodes.b}"}},
            ],
            "edges": [
                {"source": "route", "target": "a", "when": {"path": "$.nodes.route", "op": "eq", "value": "a"}},
                {"source": "route", "target": "b", "when": {"path": "$.nodes.route", "op": "eq", "value": "b"}},
                {"source": "a", "target": "result_a"},
                {"source": "b", "target": "result_b"},
            ],
        }
    )
