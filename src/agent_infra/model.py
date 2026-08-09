from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NodeSpec:
    id: str
    type: str
    config: dict[str, Any] = field(default_factory=dict)
    retry: int = 0
    timeout_ms: int | None = None
    idempotent: bool = True
    side_effect: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> NodeSpec:
        return cls(
            id=value.get("id", ""),
            type=value.get("type", ""),
            config=value.get("config", {}),
            retry=value.get("retry", 0),
            timeout_ms=value.get("timeout_ms"),
            idempotent=value.get("idempotent", True),
            side_effect=value.get("side_effect", False),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "type": self.type, "config": self.config}
        if self.retry:
            result["retry"] = self.retry
        if self.timeout_ms is not None:
            result["timeout_ms"] = self.timeout_ms
        if not self.idempotent:
            result["idempotent"] = False
        if self.side_effect:
            result["side_effect"] = True
        return result


@dataclass(frozen=True)
class EdgeSpec:
    source: str
    target: str
    when: dict[str, Any] | None = None
    max_iterations: int | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EdgeSpec:
        return cls(
            source=value.get("source", ""),
            target=value.get("target", ""),
            when=value.get("when"),
            max_iterations=value.get("max_iterations"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"source": self.source, "target": self.target}
        if self.when is not None:
            result["when"] = self.when
        if self.max_iterations is not None:
            result["max_iterations"] = self.max_iterations
        return result


@dataclass(frozen=True)
class WorkflowSpec:
    name: str
    version: str
    entry: str
    nodes: tuple[NodeSpec, ...]
    edges: tuple[EdgeSpec, ...]
    spec_version: str = "0.1"
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> WorkflowSpec:
        return cls(
            spec_version=value.get("spec_version", ""),
            name=value.get("name", ""),
            version=value.get("version", ""),
            description=value.get("description", ""),
            input_schema=value.get("input_schema", {"type": "object"}),
            output_schema=value.get("output_schema", {}),
            entry=value.get("entry", ""),
            nodes=tuple(NodeSpec.from_dict(item) for item in value.get("nodes", []) if isinstance(item, dict)),
            edges=tuple(EdgeSpec.from_dict(item) for item in value.get("edges", []) if isinstance(item, dict)),
            metadata=value.get("metadata", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "spec_version": self.spec_version,
            "name": self.name,
            "version": self.version,
            "entry": self.entry,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }
        if self.description:
            result["description"] = self.description
        if self.metadata:
            result["metadata"] = self.metadata
        return result


@dataclass(frozen=True)
class ExecutionPlan:
    format_version: str
    workflow: WorkflowSpec
    digest: str
    node_hashes: dict[str, str]
    prompt_hashes: dict[str, str]
    compiled_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "workflow": self.workflow.to_dict(),
            "digest": self.digest,
            "node_hashes": self.node_hashes,
            "prompt_hashes": self.prompt_hashes,
            "compiled_at": self.compiled_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExecutionPlan:
        return cls(
            format_version=value["format_version"],
            workflow=WorkflowSpec.from_dict(value["workflow"]),
            digest=value["digest"],
            node_hashes=value["node_hashes"],
            prompt_hashes=value.get("prompt_hashes", {}),
            compiled_at=value["compiled_at"],
        )
