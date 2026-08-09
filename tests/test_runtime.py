from __future__ import annotations

import asyncio

from helpers import branching_workflow, workflow

from agent_infra.compiler import compile_workflow
from agent_infra.model import WorkflowSpec
from agent_infra.runtime import Runtime
from agent_infra.store import Store


def test_deterministic_workflow_run_has_lineage() -> None:
    plan = compile_workflow(workflow())
    result = Runtime().run(plan, {"message": "hello"})
    assert result.status == "succeeded"
    assert result.output == {"message": "Received: hello"}
    assert result.plan_digest == plan.digest
    assert [span.node_id for span in result.node_runs] == ["render", "result"]
    assert result.node_runs[0].attributes["agent_infra.prompt.hash"] == plan.prompt_hashes["render"]


def test_runtime_caches_safe_plan_copy_and_rejects_pre_run_tampering() -> None:
    plan = compile_workflow(workflow())
    runtime = Runtime()
    assert runtime.run(plan, {"message": "first"}).output == {"message": "Received: first"}
    plan.workflow.nodes[0].config["template"] = "tampered"
    assert runtime.run(plan, {"message": "second"}).output == {"message": "Received: second"}

    tampered = compile_workflow(workflow())
    tampered.workflow.nodes[0].config["template"] = "tampered"
    try:
        Runtime().run(tampered, {"message": "third"})
    except Exception as exc:
        assert "integrity" in str(exc)
    else:
        raise AssertionError("tampered plan was accepted")


def test_branch_only_executes_matching_path() -> None:
    result = Runtime().run(compile_workflow(branching_workflow()), {"kind": "b"})
    assert result.status == "succeeded"
    assert result.output == "B"
    assert [span.node_id for span in result.node_runs] == ["route", "b", "result_b"]


def test_branch_without_matching_output_fails() -> None:
    result = Runtime().run(compile_workflow(branching_workflow()), {"kind": "unknown"})
    assert result.status == "failed"
    assert "without reaching an output" in result.error


def test_parallel_nodes_execute_concurrently_and_join() -> None:
    spec = WorkflowSpec.from_dict(
        {
            "spec_version": "0.1",
            "name": "parallel",
            "version": "1",
            "entry": "fan",
            "nodes": [
                {"id": "fan", "type": "constant", "config": {"value": True}},
                {
                    "id": "left",
                    "type": "tool",
                    "config": {"tool": "slow", "tool_version": "1", "arguments": {"value": "L"}},
                },
                {
                    "id": "right",
                    "type": "tool",
                    "config": {"tool": "slow", "tool_version": "1", "arguments": {"value": "R"}},
                },
                {"id": "join", "type": "join", "config": {"wait_for": ["left", "right"]}},
                {"id": "result", "type": "output", "config": {"value": "${$.nodes.join}"}},
            ],
            "edges": [
                {"source": "fan", "target": "left"},
                {"source": "fan", "target": "right"},
                {"source": "left", "target": "join"},
                {"source": "right", "target": "join"},
                {"source": "join", "target": "result"},
            ],
        }
    )

    async def slow(arguments: dict[str, str]) -> str:
        await asyncio.sleep(0.02)
        return arguments["value"]

    result = Runtime().register_tool("slow", slow, version="1").run(compile_workflow(spec), {})
    assert result.status == "succeeded"
    assert result.output == {"left": "L", "right": "R"}
    assert result.duration_ms < 38


def test_retry_and_content_redaction() -> None:
    attempts = 0

    def flaky(_: dict[str, object]) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")
        return "ok"

    spec = WorkflowSpec.from_dict(
        {
            "spec_version": "0.1",
            "name": "retry",
            "version": "1",
            "entry": "call",
            "nodes": [
                {
                    "id": "call",
                    "type": "tool",
                    "retry": 1,
                    "config": {"tool": "flaky", "tool_version": "unversioned", "trace_content": False},
                },
                {"id": "out", "type": "output", "config": {"value": "${$.nodes.call}"}},
            ],
            "edges": [{"source": "call", "target": "out"}],
        }
    )
    result = Runtime().register_tool("flaky", flaky).run(compile_workflow(spec), {})
    assert result.status == "succeeded"
    assert [span.status for span in result.node_runs] == ["failed", "succeeded", "succeeded"]
    assert result.node_runs[0].input is None


def test_runtime_rejects_dependency_version_drift() -> None:
    spec = WorkflowSpec.from_dict(
        {
            "spec_version": "0.1",
            "name": "versions",
            "version": "1",
            "entry": "call",
            "nodes": [
                {"id": "call", "type": "tool", "config": {"tool": "lookup", "tool_version": "2"}},
                {"id": "out", "type": "output", "config": {"value": "${$.nodes.call}"}},
            ],
            "edges": [{"source": "call", "target": "out"}],
        }
    )
    result = Runtime().register_tool("lookup", lambda _: "value", version="1").run(compile_workflow(spec), {})
    assert result.status == "failed"
    assert "version mismatch" in result.error


def test_llm_provider_records_standard_lineage_and_usage() -> None:
    spec = WorkflowSpec.from_dict(
        {
            "spec_version": "0.1",
            "name": "generation",
            "version": "1",
            "entry": "generate",
            "nodes": [
                {
                    "id": "generate",
                    "type": "llm",
                    "config": {
                        "provider": "gateway",
                        "provider_version": "2",
                        "model": "support-model",
                        "model_version": "support-model-2026-08-01",
                        "prompt": "${$.input.message}",
                    },
                },
                {"id": "out", "type": "output", "config": {"value": "${$.nodes.generate}"}},
            ],
            "edges": [{"source": "generate", "target": "out"}],
        }
    )

    def provider(request: dict[str, object]) -> dict[str, object]:
        return {
            "output": str(request["prompt"]).upper(),
            "input_tokens": 4,
            "output_tokens": 2,
            "response_model": "support-model-2026-08-01",
            "cost": 0.01,
        }

    result = (
        Runtime().register_provider("gateway", provider, version="2").run(compile_workflow(spec), {"message": "hello"})
    )
    assert result.status == "succeeded"
    assert result.output == "HELLO"
    attributes = result.node_runs[0].attributes
    assert attributes["gen_ai.usage.input_tokens"] == 4
    assert attributes["gen_ai.response.model"] == "support-model-2026-08-01"
    assert attributes["agent_infra.provider.version"] == "2"


def test_subworkflow_runs_from_immutable_plan_store(tmp_path) -> None:
    child = compile_workflow(workflow())
    parent_spec = WorkflowSpec.from_dict(
        {
            "spec_version": "0.1",
            "name": "parent",
            "version": "1",
            "entry": "child",
            "nodes": [
                {
                    "id": "child",
                    "type": "subworkflow",
                    "config": {"plan_digest": child.digest, "input": {"message": "${$.input.text}"}},
                },
                {"id": "result", "type": "output", "config": {"value": "${$.nodes.child}"}},
            ],
            "edges": [{"source": "child", "target": "result"}],
        }
    )
    parent = compile_workflow(parent_spec)
    store = Store(tmp_path / "state.db")
    store.register_plan(child)
    store.register_plan(parent)
    result = Runtime(trace_sink=store, plan_loader=store.load_plan).run(parent, {"text": "nested"})
    assert result.status == "succeeded"
    assert result.output == {"message": "Received: nested"}
    assert result.node_runs[0].attributes["agent_infra.subworkflow.plan_digest"] == child.digest
