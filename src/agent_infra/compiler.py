from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .codec import canonical_json
from .errors import ValidationFailed
from .model import ExecutionPlan, WorkflowSpec
from .validation import validate_workflow


def compile_workflow(workflow: WorkflowSpec, *, compiled_at: str | None = None) -> ExecutionPlan:
    issues = validate_workflow(workflow)
    if issues:
        summary = "; ".join(f"{issue.path}: {issue.message}" for issue in issues[:5])
        raise ValidationFailed(f"workflow validation failed: {summary}")
    normalized = workflow.to_dict()
    # Editor positions and descriptive annotations are not execution semantics. Runtime metadata
    # (for example trace capture policy) is included because it changes observable behavior.
    runtime_metadata = workflow.metadata.get("runtime") if isinstance(workflow.metadata, dict) else None
    if runtime_metadata:
        normalized["metadata"] = {"runtime": runtime_metadata}
    else:
        normalized.pop("metadata", None)
    digest = hashlib.sha256(canonical_json(normalized).encode()).hexdigest()
    node_hashes = {
        node.id: hashlib.sha256(canonical_json(node.to_dict()).encode()).hexdigest() for node in workflow.nodes
    }
    prompt_hashes = {
        node.id: hashlib.sha256(
            canonical_json(
                {
                    "system": node.config.get("system"),
                    "prompt": node.config.get("prompt"),
                    "template": node.config.get("template"),
                }
            ).encode()
        ).hexdigest()
        for node in workflow.nodes
        if node.type in {"llm", "template"}
    }
    return ExecutionPlan(
        format_version="0.1",
        workflow=workflow,
        digest=digest,
        node_hashes=node_hashes,
        prompt_hashes=prompt_hashes,
        compiled_at=compiled_at or datetime.now(timezone.utc).isoformat(),
    )
