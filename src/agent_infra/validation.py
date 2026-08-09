from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .model import WorkflowSpec

NODE_TYPES = {"constant", "template", "tool", "llm", "branch", "join", "output", "passthrough", "subworkflow"}
OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "exists", "truthy"}
ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
TOP_LEVEL_FIELDS = {
    "spec_version",
    "name",
    "version",
    "description",
    "input_schema",
    "output_schema",
    "entry",
    "nodes",
    "edges",
    "metadata",
}
NODE_FIELDS = {"id", "type", "config", "retry", "timeout_ms", "idempotent", "side_effect"}
EDGE_FIELDS = {"source", "target", "when", "max_iterations"}


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "message": self.message, "severity": self.severity}


def _issue(issues: list[ValidationIssue], path: str, code: str, message: str) -> None:
    issues.append(ValidationIssue(path, code, message))


def validate_workflow(workflow: WorkflowSpec) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if workflow.spec_version != "0.1":
        _issue(issues, "$.spec_version", "unsupported_version", "supported spec_version is '0.1'")
    for key, value in (("name", workflow.name), ("version", workflow.version), ("entry", workflow.entry)):
        if not value:
            _issue(issues, f"$.{key}", "required", f"{key} is required")
    if workflow.name and not ID_PATTERN.fullmatch(workflow.name):
        _issue(issues, "$.name", "invalid_id", "name must start with a letter and contain letters, digits, _ or -")
    if not isinstance(workflow.input_schema, dict):
        _issue(issues, "$.input_schema", "invalid_type", "input_schema must be an object")
    if not isinstance(workflow.output_schema, dict):
        _issue(issues, "$.output_schema", "invalid_type", "output_schema must be an object")
    runtime_metadata = workflow.metadata.get("runtime") if isinstance(workflow.metadata, dict) else None
    if runtime_metadata is not None and not isinstance(runtime_metadata, dict):
        _issue(issues, "$.metadata.runtime", "invalid_type", "metadata.runtime must be an object")
    elif isinstance(runtime_metadata, dict):
        for key in ("trace_input", "trace_output"):
            if key in runtime_metadata and not isinstance(runtime_metadata[key], bool):
                _issue(issues, f"$.metadata.runtime.{key}", "invalid_type", f"{key} must be boolean")

    ids: set[str] = set()
    for index, node in enumerate(workflow.nodes):
        path = f"$.nodes[{index}]"
        if not ID_PATTERN.fullmatch(node.id):
            _issue(
                issues,
                f"{path}.id",
                "invalid_id",
                "node id must start with a letter and contain letters, digits, _ or -",
            )
        elif node.id in ids:
            _issue(issues, f"{path}.id", "duplicate_id", f"duplicate node id {node.id!r}")
        ids.add(node.id)
        if node.type not in NODE_TYPES:
            _issue(issues, f"{path}.type", "unknown_node_type", f"node type must be one of {sorted(NODE_TYPES)}")
        if not isinstance(node.config, dict):
            _issue(issues, f"{path}.config", "invalid_type", "config must be an object")
        if not isinstance(node.retry, int) or node.retry < 0 or node.retry > 10:
            _issue(issues, f"{path}.retry", "invalid_retry", "retry must be an integer from 0 to 10")
        if node.timeout_ms is not None and (not isinstance(node.timeout_ms, int) or node.timeout_ms <= 0):
            _issue(issues, f"{path}.timeout_ms", "invalid_timeout", "timeout_ms must be a positive integer")
        if node.side_effect and node.retry and not node.idempotent:
            _issue(issues, path, "unsafe_retry", "a side-effecting non-idempotent node cannot be retried")
        _validate_node_config(node.type, node.config, path, issues)

    if workflow.entry and workflow.entry not in ids:
        _issue(issues, "$.entry", "unknown_node", f"entry references unknown node {workflow.entry!r}")

    adjacency: dict[str, list[str]] = {node_id: [] for node_id in ids}
    edge_keys: set[tuple[str, str, str]] = set()
    for index, edge in enumerate(workflow.edges):
        path = f"$.edges[{index}]"
        if edge.source not in ids:
            _issue(issues, f"{path}.source", "unknown_node", f"unknown source node {edge.source!r}")
        if edge.target not in ids:
            _issue(issues, f"{path}.target", "unknown_node", f"unknown target node {edge.target!r}")
        if edge.source in ids and edge.target in ids:
            adjacency[edge.source].append(edge.target)
        if edge.when is not None:
            _validate_condition(edge.when, f"{path}.when", issues)
        edge_key = (edge.source, edge.target, repr(edge.when))
        if edge_key in edge_keys:
            _issue(issues, path, "duplicate_edge", "duplicate edge")
        edge_keys.add(edge_key)
        if edge.max_iterations is not None and (
            not isinstance(edge.max_iterations, int) or edge.max_iterations < 1 or edge.max_iterations > 1000
        ):
            _issue(issues, f"{path}.max_iterations", "invalid_iterations", "max_iterations must be 1..1000")

    reachable = _reachable(workflow.entry, adjacency) if workflow.entry in ids else set()
    for node_id in sorted(ids - reachable):
        _issue(issues, f"$.nodes[{node_id}]", "unreachable_node", f"node {node_id!r} is not reachable from entry")

    cyclic_edges = _cyclic_edges(adjacency)
    bounded = {(edge.source, edge.target) for edge in workflow.edges if edge.max_iterations is not None}
    for source, target in sorted(cyclic_edges - bounded):
        _issue(
            issues,
            "$.edges",
            "unbounded_cycle",
            f"cycle edge {source!r} -> {target!r} requires max_iterations",
        )
    if not any(node.type == "output" for node in workflow.nodes):
        _issue(issues, "$.nodes", "missing_output", "workflow requires at least one output node")
    for node in workflow.nodes:
        if node.type == "output" and adjacency.get(node.id):
            _issue(issues, f"$.nodes[{node.id}]", "output_has_edges", "output nodes cannot have outgoing edges")
        if node.type == "join" and isinstance(node.config, dict):
            for dependency in node.config.get("wait_for", []):
                if dependency not in ids:
                    _issue(
                        issues,
                        f"$.nodes[{node.id}].config.wait_for",
                        "unknown_node",
                        f"join references unknown node {dependency!r}",
                    )
    return issues


def validate_document(value: Any) -> list[ValidationIssue]:
    """Check source-level shape before dataclass parsing can normalize fields."""
    issues: list[ValidationIssue] = []
    if not isinstance(value, dict):
        return [ValidationIssue("$", "invalid_type", "workflow document must be an object")]
    for field in ("spec_version", "name", "version", "entry", "nodes", "edges"):
        if field not in value:
            _issue(issues, f"$.{field}", "required", f"{field} is required")
    for field in value.keys() - TOP_LEVEL_FIELDS:
        _issue(issues, f"$.{field}", "unknown_field", "unknown workflow field")
    for field in ("spec_version", "name", "version", "entry"):
        if field in value and not isinstance(value[field], str):
            _issue(issues, f"$.{field}", "invalid_type", f"{field} must be a string")
    for field in ("input_schema", "output_schema", "metadata"):
        if field in value and not isinstance(value[field], dict):
            _issue(issues, f"$.{field}", "invalid_type", f"{field} must be an object")
    for collection, allowed in (("nodes", NODE_FIELDS), ("edges", EDGE_FIELDS)):
        items = value.get(collection, [])
        if not isinstance(items, list):
            _issue(issues, f"$.{collection}", "invalid_type", f"{collection} must be an array")
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                _issue(issues, f"$.{collection}[{index}]", "invalid_type", "item must be an object")
                continue
            for field in item.keys() - allowed:
                _issue(issues, f"$.{collection}[{index}].{field}", "unknown_field", f"unknown {collection[:-1]} field")
    if not issues:
        issues.extend(validate_workflow(WorkflowSpec.from_dict(value)))
    return issues


def _validate_node_config(kind: str, config: Any, path: str, issues: list[ValidationIssue]) -> None:
    if not isinstance(config, dict):
        return
    required: dict[str, tuple[str, ...]] = {
        "constant": ("value",),
        "template": ("template",),
        "tool": ("tool", "tool_version"),
        "llm": ("provider", "provider_version", "model", "model_version", "prompt"),
        "branch": ("value",),
        "join": ("wait_for",),
        "output": ("value",),
        "subworkflow": ("plan_digest", "input"),
    }
    for key in required.get(kind, ()):
        if key not in config:
            _issue(issues, f"{path}.config.{key}", "required", f"{kind} node requires config.{key}")
    if (
        kind == "join"
        and "wait_for" in config
        and not (isinstance(config["wait_for"], list) and all(isinstance(item, str) for item in config["wait_for"]))
    ):
        _issue(issues, f"{path}.config.wait_for", "invalid_type", "wait_for must be an array of node ids")


def _validate_condition(value: Any, path: str, issues: list[ValidationIssue]) -> None:
    if not isinstance(value, dict):
        _issue(issues, path, "invalid_condition", "condition must be an object")
        return
    if not isinstance(value.get("path"), str):
        _issue(issues, f"{path}.path", "required", "condition path must be a string")
    if value.get("op") not in OPERATORS:
        _issue(issues, f"{path}.op", "invalid_operator", f"condition op must be one of {sorted(OPERATORS)}")


def _reachable(entry: str, adjacency: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    stack = [entry]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(adjacency.get(node, ()))
    return seen


def _cyclic_edges(adjacency: dict[str, list[str]]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for source, targets in adjacency.items():
        for target in targets:
            if source in _reachable(target, adjacency):
                result.add((source, target))
    return result


def validate_json_value(value: Any, schema: dict[str, Any], path: str = "$") -> list[ValidationIssue]:
    """Validate the useful, dependency-free subset of JSON Schema 2020-12."""
    issues: list[ValidationIssue] = []
    expected = schema.get("type")
    type_checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if expected in type_checks and not type_checks[expected](value):
        _issue(issues, path, "schema_type", f"expected {expected}, got {type(value).__name__}")
        return issues
    if "enum" in schema and value not in schema["enum"]:
        _issue(issues, path, "schema_enum", f"value must be one of {schema['enum']!r}")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                _issue(issues, f"{path}.{name}", "schema_required", "required property is missing")
        properties = schema.get("properties", {})
        for name, child in properties.items():
            if name in value and isinstance(child, dict):
                issues.extend(validate_json_value(value[name], child, f"{path}.{name}"))
        if schema.get("additionalProperties") is False:
            for name in value.keys() - properties.keys():
                _issue(issues, f"{path}.{name}", "schema_additional", "additional property is not allowed")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            issues.extend(validate_json_value(item, schema["items"], f"{path}[{index}]"))
    return issues
