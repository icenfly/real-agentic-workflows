from __future__ import annotations

import sqlite3

import pytest
from helpers import workflow

from agent_infra.compiler import compile_workflow
from agent_infra.errors import StoreError
from agent_infra.experiments import ExperimentManager
from agent_infra.model import WorkflowSpec
from agent_infra.runtime import Runtime
from agent_infra.store import Store


def test_store_enforces_immutable_named_version(tmp_path) -> None:
    store = Store(tmp_path / "state.db")
    store.register_plan(compile_workflow(workflow()))
    with pytest.raises(StoreError, match="immutable"):
        store.register_plan(compile_workflow(workflow(prefix="Changed")))


def test_store_honors_workflow_trace_content_policy(tmp_path) -> None:
    value = workflow().to_dict()
    value["metadata"] = {"runtime": {"trace_input": False, "trace_output": False}}
    plan = compile_workflow(WorkflowSpec.from_dict(value))
    store = Store(tmp_path / "state.db")
    store.register_plan(plan)
    run = Runtime(trace_sink=store).run(plan, {"message": "secret"})
    assert run.output == {"message": "Received: secret"}
    stored = store.get_run(run.run_id)
    assert stored["input"] is None
    assert stored["output"] is None


def test_assignment_is_sticky_and_exposure_requires_matching_run(tmp_path) -> None:
    store = Store(tmp_path / "state.db")
    manager = ExperimentManager(store)
    control = compile_workflow(workflow("1.0.0", "Control"))
    treatment = compile_workflow(workflow("2.0.0", "Treatment"))
    manager.start(
        name="reply_test",
        assignment_unit="organization_id",
        primary_metric="resolved",
        variations=[("control", control, 1), ("treatment", treatment, 1)],
    )
    first = manager.assign("reply_test", unit_name="organization_id", unit_value="acme")
    second = manager.assign("reply_test", unit_name="organization_id", unit_value="acme")
    assert first.assignment_id == second.assignment_id
    assert first.variation_key == second.variation_key

    plan = store.load_plan(first.plan_digest)
    result = Runtime(trace_sink=store).run(plan, {"message": "hello"}, experiment=first.lineage())
    manager.expose(first, result.run_id)
    manager.outcome(first.assignment_id, metric="resolved", value=1)
    status = manager.status("reply_test")
    assert status["health"]["exposures"] == 1
    assert status["metrics"][0]["mean"] == 1


def test_promote_and_rollback(tmp_path) -> None:
    store = Store(tmp_path / "state.db")
    manager = ExperimentManager(store)
    v1 = compile_workflow(workflow("1", "One"))
    v2 = compile_workflow(workflow("2", "Two"))
    manager.start(
        name="release_1",
        assignment_unit="account_id",
        primary_metric="quality",
        variations=[("one", v1, 1), ("two", v2, 1)],
    )
    manager.promote("release_1", "one")
    manager.start(
        name="release_2",
        assignment_unit="account_id",
        primary_metric="quality",
        variations=[("one", v1, 1), ("two", v2, 1)],
    )
    deployed = manager.promote("release_2", "two")
    assert deployed["plan_digest"] == v2.digest
    rolled_back = manager.rollback("support_flow")
    assert rolled_back["plan_digest"] == v1.digest


def test_store_migrates_pre_iteration_variations_table(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE variations (experiment_id TEXT, variation_key TEXT, plan_digest TEXT, weight INTEGER)"
    )
    connection.commit()
    connection.close()
    store = Store(path)
    columns = {row["name"] for row in store.query_all("PRAGMA table_info(variations)")}
    assert "iteration" in columns


def test_experiment_start_is_idempotent_and_iteration_preserves_history(tmp_path) -> None:
    store = Store(tmp_path / "state.db")
    manager = ExperimentManager(store)
    v1 = compile_workflow(workflow("1", "One"))
    v2 = compile_workflow(workflow("2", "Two"))
    definition = dict(
        name="iterations",
        assignment_unit="case_id",
        primary_metric="quality",
        variations=[("one", v1, 1), ("two", v2, 1)],
    )
    first_status = manager.start(**definition)
    assert manager.start(**definition)["experiment_id"] == first_status["experiment_id"]
    first = manager.assign("iterations", unit_name="case_id", unit_value="C-1")
    manager.stop("iterations")
    v3 = compile_workflow(workflow("3", "Three"))
    status = manager.iterate("iterations", [("one", v1, 1), ("three", v3, 2)])
    second = manager.assign("iterations", unit_name="case_id", unit_value="C-1")
    assert status["iteration"] == 2
    assert second.iteration == 2
    assert second.assignment_id != first.assignment_id
    historic = store.query_all(
        "SELECT iteration,variation_key,plan_digest FROM variations WHERE experiment_id=? "
        "ORDER BY iteration,variation_key",
        (first.experiment_id,),
    )
    assert len(historic) == 4


def test_outcome_idempotency_key_prevents_double_count(tmp_path) -> None:
    store = Store(tmp_path / "state.db")
    manager = ExperimentManager(store)
    v1 = compile_workflow(workflow("1", "One"))
    v2 = compile_workflow(workflow("2", "Two"))
    manager.start(
        name="outcomes",
        assignment_unit="ticket_id",
        primary_metric="resolved",
        variations=[("one", v1, 1), ("two", v2, 1)],
    )
    assignment = manager.assign("outcomes", unit_name="ticket_id", unit_value="T-1")
    selected = store.load_plan(assignment.plan_digest)
    run = Runtime(trace_sink=store).run(selected, {"message": "hello"}, experiment=assignment.lineage())
    manager.expose(assignment, run.run_id)
    first = manager.outcome(assignment.assignment_id, metric="resolved", value=1, idempotency_key="event-1")
    assert manager.outcome(assignment.assignment_id, metric="resolved", value=1, idempotency_key="event-1") == first
    assert len(store.query_all("SELECT * FROM outcomes")) == 1


def test_guardrail_violation_automatically_pauses_experiment(tmp_path) -> None:
    store = Store(tmp_path / "state.db")
    manager = ExperimentManager(store)
    v1 = compile_workflow(workflow("1", "One"))
    v2 = compile_workflow(workflow("2", "Two"))
    manager.start(
        name="guarded",
        assignment_unit="ticket_id",
        primary_metric="resolved",
        variations=[("one", v1, 1), ("two", v2, 1)],
        guardrails=[{"metric": "policy_violation", "direction": "max", "threshold": 0, "min_units": 1}],
    )
    assignment = manager.assign("guarded", unit_name="ticket_id", unit_value="T-1")
    selected = store.load_plan(assignment.plan_digest)
    run = Runtime(trace_sink=store).run(selected, {"message": "hello"}, experiment=assignment.lineage())
    manager.expose(assignment, run.run_id)
    manager.outcome(assignment.assignment_id, metric="policy_violation", value=1)
    status = manager.status("guarded")
    assert status["status"] == "stopped"
    assert any(rule["violated"] for rule in status["guardrails"])
    with pytest.raises(StoreError, match="not running"):
        manager.assign("guarded", unit_name="ticket_id", unit_value="T-2")
    assert "experiment.guardrail_pause" in {event["action"] for event in store.list_audit()}
