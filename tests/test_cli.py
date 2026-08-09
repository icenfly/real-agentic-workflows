from __future__ import annotations

import json

from helpers import workflow

from agent_infra.cli import main
from agent_infra.codec import write_json


def test_cli_init_validate_compile_run_trace(tmp_path, capsys) -> None:
    project = tmp_path / "project"
    database = tmp_path / "state.db"
    prefix = ["--db", str(database)]
    assert main(prefix + ["init", str(project), "--name", "cli_flow"]) == 0
    capsys.readouterr()
    assert main(prefix + ["validate", str(project / "workflow.json")]) == 0
    capsys.readouterr()
    plan = project / "plan.json"
    assert main(prefix + ["compile", str(project / "workflow.json"), "-o", str(plan)]) == 0
    capsys.readouterr()
    assert main(prefix + ["run", str(plan), "--input", '{"message":"hi"}']) == 0
    run_payload = json.loads(capsys.readouterr().out)
    assert run_payload["output"] == {"message": "Received: hi"}
    assert main(prefix + ["trace", run_payload["run_id"]]) == 0
    trace = json.loads(capsys.readouterr().out)
    assert trace["plan_digest"] == run_payload["plan_digest"]
    assert len(trace["node_runs"]) == 2
    assert main(prefix + ["deploy", str(plan)]) == 0
    capsys.readouterr()
    assert main(prefix + ["audit"]) == 0
    audit = json.loads(capsys.readouterr().out)
    assert audit["events"][0]["action"] == "deploy"


def test_cli_validate_rejects_unknown_source_fields(tmp_path, capsys) -> None:
    source = tmp_path / "bad.json"
    source.write_text(
        json.dumps(
            {
                "spec_version": "0.1",
                "name": "bad",
                "version": "1",
                "entry": "out",
                "nodes": [{"id": "out", "type": "output", "config": {"value": 1}, "surprise": True}],
                "edges": [],
            }
        )
    )
    assert main(["validate", str(source)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["issues"][0]["code"] == "unknown_field"


def test_cli_online_experiment_lifecycle(tmp_path, capsys) -> None:
    database = tmp_path / "state.db"
    control = tmp_path / "control.json"
    treatment = tmp_path / "treatment.json"
    write_json(control, workflow("1", "Control").to_dict())
    write_json(treatment, workflow("2", "Treatment").to_dict())
    config = tmp_path / "experiment.json"
    write_json(
        config,
        {
            "name": "cli_experiment",
            "assignment_unit": "organization_id",
            "primary_metric": "resolved",
            "guardrails": [{"metric": "agent.error", "direction": "max", "threshold": 0.5, "min_units": 10}],
            "variations": [
                {"key": "control", "source": "control.json", "weight": 1},
                {"key": "treatment", "source": "treatment.json", "weight": 1},
            ],
        },
    )
    prefix = ["--db", str(database)]
    assert main(prefix + ["deploy", str(control)]) == 0
    capsys.readouterr()
    assert main(prefix + ["experiment", "start", str(config), "--dry-run"]) == 0
    capsys.readouterr()
    assert main(prefix + ["experiment", "start", str(config)]) == 0
    capsys.readouterr()
    assert (
        main(
            prefix
            + [
                "run",
                "--experiment",
                "cli_experiment",
                "--unit-name",
                "organization_id",
                "--unit-value",
                "acme",
                "--input",
                '{"message":"hello"}',
            ]
        )
        == 0
    )
    run = json.loads(capsys.readouterr().out)
    assignment_id = run["experiment"]["assignment_id"]
    assert main(prefix + ["outcome", assignment_id, "resolved", "1", "--idempotency-key", "case-1"]) == 0
    capsys.readouterr()
    assert main(prefix + ["experiment", "status", "cli_experiment"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["health"]["exposures"] == 1
    assert {item["metric"] for item in status["metrics"]} >= {"resolved", "agent.error", "agent.latency_ms"}
    assert main(prefix + ["experiment", "stop", "cli_experiment"]) == 0
    capsys.readouterr()
    assert main(prefix + ["experiment", "promote", "cli_experiment", "treatment"]) == 0
    promoted = json.loads(capsys.readouterr().out)
    assert promoted["previous_digest"] is not None
    assert main(prefix + ["rollback", "support_flow"]) == 0
    rolled_back = json.loads(capsys.readouterr().out)
    assert rolled_back["plan_digest"] == promoted["previous_digest"]
