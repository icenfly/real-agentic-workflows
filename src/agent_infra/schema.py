from __future__ import annotations

from typing import Any

WORKFLOW_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://raw.githubusercontent.com/icenfly/real-agentic-workflows/main/schema/workflow-0.1.json",
    "title": "REAL Agentic Workflow",
    "type": "object",
    "additionalProperties": False,
    "required": ["spec_version", "name", "version", "entry", "nodes", "edges"],
    "properties": {
        "spec_version": {"const": "0.1"},
        "name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,127}$"},
        "version": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "entry": {"type": "string"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "metadata": {"type": "object"},
        "nodes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "type", "config"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,127}$"},
                    "type": {
                        "enum": [
                            "constant",
                            "template",
                            "tool",
                            "llm",
                            "branch",
                            "join",
                            "output",
                            "passthrough",
                            "subworkflow",
                        ]
                    },
                    "config": {"type": "object"},
                    "retry": {"type": "integer", "minimum": 0, "maximum": 10},
                    "timeout_ms": {"type": "integer", "minimum": 1},
                    "idempotent": {"type": "boolean"},
                    "side_effect": {"type": "boolean"},
                },
                "allOf": [
                    {
                        "if": {"properties": {"type": {"const": "tool"}}},
                        "then": {"properties": {"config": {"required": ["tool", "tool_version"]}}},
                    },
                    {
                        "if": {"properties": {"type": {"const": "llm"}}},
                        "then": {
                            "properties": {
                                "config": {
                                    "required": ["provider", "provider_version", "model", "model_version", "prompt"]
                                }
                            }
                        },
                    },
                    {
                        "if": {"properties": {"type": {"const": "subworkflow"}}},
                        "then": {"properties": {"config": {"required": ["plan_digest", "input"]}}},
                    },
                    {
                        "if": {"properties": {"type": {"enum": ["constant", "branch", "output"]}}},
                        "then": {"properties": {"config": {"required": ["value"]}}},
                    },
                    {
                        "if": {"properties": {"type": {"const": "template"}}},
                        "then": {"properties": {"config": {"required": ["template"]}}},
                    },
                    {
                        "if": {"properties": {"type": {"const": "join"}}},
                        "then": {"properties": {"config": {"required": ["wait_for"]}}},
                    },
                ],
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "target"],
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "max_iterations": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "when": {
                        "type": "object",
                        "required": ["path", "op"],
                        "properties": {
                            "path": {"type": "string"},
                            "op": {
                                "enum": ["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "exists", "truthy"]
                            },
                            "value": {},
                        },
                    },
                },
            },
        },
    },
}
