from __future__ import annotations

from helpers import workflow

from agent_infra.compiler import compile_workflow
from agent_infra.model import WorkflowSpec
from agent_infra.validation import validate_workflow


def test_compile_is_reproducible_and_tracks_prompt_hash() -> None:
    first = compile_workflow(workflow(), compiled_at="2026-01-01T00:00:00Z")
    second = compile_workflow(workflow(), compiled_at="2026-02-01T00:00:00Z")
    assert first.digest == second.digest
    assert first.node_hashes == second.node_hashes
    assert set(first.prompt_hashes) == {"render"}


def test_content_change_changes_digest() -> None:
    assert compile_workflow(workflow()).digest != compile_workflow(workflow(prefix="Handled")).digest


def test_canvas_layout_does_not_change_execution_digest() -> None:
    original = workflow()
    value = original.to_dict()
    value["metadata"] = {"canvas": {"positions": {"render": {"x": 10, "y": 20}}}}
    assert compile_workflow(original).digest == compile_workflow(WorkflowSpec.from_dict(value)).digest


def test_validation_finds_unknown_edge_and_unreachable_node() -> None:
    spec = WorkflowSpec.from_dict(
        {
            "spec_version": "0.1",
            "name": "bad",
            "version": "1",
            "entry": "start",
            "nodes": [
                {"id": "start", "type": "constant", "config": {"value": 1}},
                {"id": "orphan", "type": "constant", "config": {"value": 2}},
            ],
            "edges": [{"source": "start", "target": "missing"}],
        }
    )
    codes = {issue.code for issue in validate_workflow(spec)}
    assert "unknown_node" in codes
    assert "unreachable_node" in codes


def test_validation_rejects_unbounded_cycle() -> None:
    spec = WorkflowSpec.from_dict(
        {
            "spec_version": "0.1",
            "name": "cycle",
            "version": "1",
            "entry": "a",
            "nodes": [
                {"id": "a", "type": "constant", "config": {"value": 1}},
                {"id": "b", "type": "constant", "config": {"value": 2}},
            ],
            "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
        }
    )
    assert any(issue.code == "unbounded_cycle" for issue in validate_workflow(spec))
