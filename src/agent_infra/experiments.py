from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .errors import StoreError
from .model import ExecutionPlan
from .runtime import RunResult
from .store import Store
from .validation import ID_PATTERN


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Assignment:
    assignment_id: str
    experiment_id: str
    experiment_name: str
    iteration: int
    assignment_unit: str
    unit_value: str
    variation_key: str
    plan_digest: str

    def lineage(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "experiment": self.experiment_name,
            "iteration": self.iteration,
            "assignment_id": self.assignment_id,
            "assignment_unit": self.assignment_unit,
            "assignment_value": self.unit_value,
            "variation": self.variation_key,
        }


class ExperimentManager:
    def __init__(self, store: Store, *, actor: str = "system") -> None:
        self.store = store
        self.actor = actor

    def start(
        self,
        *,
        name: str,
        assignment_unit: str,
        primary_metric: str,
        variations: list[tuple[str, ExecutionPlan, int]],
        guardrails: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if len(variations) < 2:
            raise StoreError("an experiment requires at least two variations")
        if not ID_PATTERN.fullmatch(name):
            raise StoreError("experiment name must start with a letter and contain letters, digits, _ or -")
        if not ID_PATTERN.fullmatch(assignment_unit):
            raise StoreError("assignment_unit must start with a letter and contain letters, digits, _ or -")
        if not primary_metric:
            raise StoreError("assignment_unit and primary_metric are required")
        keys = [key for key, _, _ in variations]
        if any(not ID_PATTERN.fullmatch(key) for key in keys):
            raise StoreError("variation keys must start with a letter and contain letters, digits, _ or -")
        if len(keys) != len(set(keys)):
            raise StoreError("variation keys must be unique")
        workflow_names = {plan.workflow.name for _, plan, _ in variations}
        if len(workflow_names) != 1:
            raise StoreError("all variations must belong to the same workflow name")
        if any(not isinstance(weight, int) or isinstance(weight, bool) or weight <= 0 for _, _, weight in variations):
            raise StoreError("variation weights must be positive integers")
        normalized_guardrails = self._validate_guardrails(guardrails or [])
        for _, plan, _ in variations:
            self.store.register_plan(plan)
        existing_rows = self.store.query_all("SELECT * FROM experiments WHERE name=?", (name,))
        if existing_rows:
            existing = dict(existing_rows[0])
            current = self.store.query_all(
                "SELECT variation_key,plan_digest,weight FROM variations "
                "WHERE experiment_id=? AND iteration=? ORDER BY variation_key",
                (existing["experiment_id"], existing["iteration"]),
            )
            proposed = sorted((key, plan.digest, weight) for key, plan, weight in variations)
            actual = [(row["variation_key"], row["plan_digest"], row["weight"]) for row in current]
            if (
                existing["assignment_unit"] == assignment_unit
                and existing["primary_metric"] == primary_metric
                and existing["workflow_name"] == next(iter(workflow_names))
                and json.loads(existing["guardrails_json"]) == normalized_guardrails
                and actual == proposed
            ):
                return self.status(name)
            raise StoreError(f"experiment {name!r} already exists with a different definition; create a new iteration")
        experiment_id = uuid.uuid4().hex
        now = _now()
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO experiments "
                "(experiment_id,name,workflow_name,assignment_unit,primary_metric,guardrails_json,"
                "status,iteration,created_at) "
                "VALUES(?,?,?,?,?,?,'running',1,?)",
                (
                    experiment_id,
                    name,
                    next(iter(workflow_names)),
                    assignment_unit,
                    primary_metric,
                    json.dumps(normalized_guardrails, sort_keys=True),
                    now,
                ),
            )
            for key, plan, weight in variations:
                connection.execute(
                    "INSERT INTO variations VALUES(?,?,?,?,?)", (experiment_id, 1, key, plan.digest, weight)
                )
            self.store.record_audit(
                connection,
                action="experiment.start",
                resource_type="experiment",
                resource_id=name,
                actor=self.actor,
                recorded_at=now,
                details={"experiment_id": experiment_id, "iteration": 1},
            )
        return self.status(name)

    def iterate(
        self,
        name: str,
        variations: list[tuple[str, ExecutionPlan, int]],
        *,
        guardrails: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if len(variations) < 2:
            raise StoreError("an experiment iteration requires at least two variations")
        for _, plan, weight in variations:
            if not isinstance(weight, int) or isinstance(weight, bool) or weight <= 0:
                raise StoreError("variation weights must be positive integers")
            self.store.register_plan(plan)
        with self.store.transaction() as connection:
            experiment = connection.execute("SELECT * FROM experiments WHERE name=?", (name,)).fetchone()
            if experiment is None:
                raise StoreError(f"unknown experiment {name!r}")
            if experiment["status"] == "running":
                raise StoreError("stop the running experiment before creating its next iteration")
            workflow_names = {plan.workflow.name for _, plan, _ in variations}
            if workflow_names != {experiment["workflow_name"]}:
                raise StoreError("all iteration variations must retain the experiment workflow name")
            keys = [key for key, _, _ in variations]
            if len(keys) != len(set(keys)):
                raise StoreError("variation keys must be unique")
            if any(not ID_PATTERN.fullmatch(key) for key in keys):
                raise StoreError("variation keys must start with a letter and contain letters, digits, _ or -")
            iteration = experiment["iteration"] + 1
            normalized_guardrails = (
                self._validate_guardrails(guardrails)
                if guardrails is not None
                else json.loads(experiment["guardrails_json"])
            )
            for key, plan, weight in variations:
                connection.execute(
                    "INSERT INTO variations VALUES(?,?,?,?,?)",
                    (experiment["experiment_id"], iteration, key, plan.digest, weight),
                )
            connection.execute(
                "UPDATE experiments SET status='running',iteration=?,guardrails_json=?,"
                "stopped_at=NULL,promoted_variation=NULL "
                "WHERE experiment_id=?",
                (iteration, json.dumps(normalized_guardrails, sort_keys=True), experiment["experiment_id"]),
            )
            self.store.record_audit(
                connection,
                action="experiment.iterate",
                resource_type="experiment",
                resource_id=name,
                actor=self.actor,
                recorded_at=_now(),
                details={"experiment_id": experiment["experiment_id"], "iteration": iteration},
            )
        return self.status(name)

    def assign(self, name: str, *, unit_name: str, unit_value: str) -> Assignment:
        if not unit_value or len(unit_value) > 512:
            raise StoreError("assignment unit value must contain 1..512 characters")
        with self.store.transaction() as connection:
            experiment = connection.execute("SELECT * FROM experiments WHERE name=?", (name,)).fetchone()
            if experiment is None:
                raise StoreError(f"unknown experiment {name!r}")
            if experiment["status"] != "running":
                raise StoreError(f"experiment {name!r} is not running")
            if experiment["assignment_unit"] != unit_name:
                raise StoreError(f"experiment randomizes by {experiment['assignment_unit']!r}, not {unit_name!r}")
            existing = connection.execute(
                "SELECT * FROM assignments WHERE experiment_id=? AND iteration=? AND unit_value=?",
                (experiment["experiment_id"], experiment["iteration"], unit_value),
            ).fetchone()
            variations = connection.execute(
                "SELECT * FROM variations WHERE experiment_id=? AND iteration=? ORDER BY variation_key",
                (experiment["experiment_id"], experiment["iteration"]),
            ).fetchall()
            if existing is None:
                total = sum(row["weight"] for row in variations)
                material = f"{experiment['experiment_id']}:{experiment['iteration']}:{unit_value}".encode()
                bucket = int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % total
                cursor = 0
                selected = variations[-1]
                for variation in variations:
                    cursor += variation["weight"]
                    if bucket < cursor:
                        selected = variation
                        break
                assignment_id = uuid.uuid4().hex
                connection.execute(
                    "INSERT INTO assignments VALUES(?,?,?,?,?,?)",
                    (
                        assignment_id,
                        experiment["experiment_id"],
                        experiment["iteration"],
                        unit_value,
                        selected["variation_key"],
                        _now(),
                    ),
                )
                variation_key = selected["variation_key"]
            else:
                assignment_id = existing["assignment_id"]
                variation_key = existing["variation_key"]
            selected = next(row for row in variations if row["variation_key"] == variation_key)
        return Assignment(
            assignment_id=assignment_id,
            experiment_id=experiment["experiment_id"],
            experiment_name=experiment["name"],
            iteration=experiment["iteration"],
            assignment_unit=unit_name,
            unit_value=unit_value,
            variation_key=variation_key,
            plan_digest=selected["plan_digest"],
        )

    def expose(self, assignment: Assignment, run_id: str) -> str:
        exposure_id = uuid.uuid4().hex
        with self.store.transaction() as connection:
            run = connection.execute("SELECT plan_digest FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                raise StoreError("cannot record exposure before the workflow run is recorded")
            if run["plan_digest"] != assignment.plan_digest:
                raise StoreError("run plan does not match the assigned variation")
            existing = connection.execute("SELECT * FROM exposures WHERE run_id=?", (run_id,)).fetchone()
            if existing:
                if existing["assignment_id"] != assignment.assignment_id:
                    raise StoreError("run is already exposed under another assignment")
                return existing["exposure_id"]
            connection.execute(
                "INSERT INTO exposures VALUES(?,?,?,?)", (exposure_id, assignment.assignment_id, run_id, _now())
            )
        return exposure_id

    def outcome(
        self,
        assignment_id: str,
        *,
        metric: str,
        value: float,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        if not metric or len(metric) > 128:
            raise StoreError("metric must contain 1..128 characters")
        if not math.isfinite(float(value)):
            raise StoreError("outcome value must be finite")
        if metadata is not None and not isinstance(metadata, dict):
            raise StoreError("outcome metadata must be an object")
        if idempotency_key is not None and (not idempotency_key or len(idempotency_key) > 255):
            raise StoreError("outcome idempotency_key must contain 1..255 characters")
        outcome_id = (
            "out_" + hashlib.sha256(f"{assignment_id}:{metric}:{idempotency_key}".encode()).hexdigest()
            if idempotency_key
            else uuid.uuid4().hex
        )
        with self.store.transaction() as connection:
            if (
                connection.execute("SELECT 1 FROM assignments WHERE assignment_id=?", (assignment_id,)).fetchone()
                is None
            ):
                raise StoreError(f"unknown assignment {assignment_id!r}")
            if (
                connection.execute("SELECT 1 FROM exposures WHERE assignment_id=? LIMIT 1", (assignment_id,)).fetchone()
                is None
            ):
                raise StoreError("cannot attribute an outcome before the assignment has an exposure")
            existing = connection.execute("SELECT * FROM outcomes WHERE outcome_id=?", (outcome_id,)).fetchone()
            if existing:
                if (
                    existing["assignment_id"] != assignment_id
                    or existing["metric"] != metric
                    or existing["value"] != float(value)
                ):
                    raise StoreError("idempotency key was already used with a different outcome")
                return outcome_id
            connection.execute(
                "INSERT INTO outcomes VALUES(?,?,?,?,?,?)",
                (outcome_id, assignment_id, metric, float(value), _now(), json.dumps(metadata or {})),
            )
            experiment_name = connection.execute(
                "SELECT e.name FROM experiments e JOIN assignments a ON a.experiment_id=e.experiment_id "
                "WHERE a.assignment_id=?",
                (assignment_id,),
            ).fetchone()["name"]
        self._pause_on_guardrail(experiment_name)
        return outcome_id

    def record_run_metrics(self, assignment: Assignment, result: RunResult) -> list[str]:
        outcome_ids = [
            self.outcome(
                assignment.assignment_id,
                metric="agent.error",
                value=0 if result.status == "succeeded" else 1,
                idempotency_key=f"{result.run_id}:error",
            ),
            self.outcome(
                assignment.assignment_id,
                metric="agent.latency_ms",
                value=result.duration_ms,
                idempotency_key=f"{result.run_id}:latency",
            ),
        ]
        costs = [
            span.attributes.get("gen_ai.usage.cost")
            for span in result.node_runs
            if isinstance(span.attributes.get("gen_ai.usage.cost"), (int, float))
        ]
        if costs:
            outcome_ids.append(
                self.outcome(
                    assignment.assignment_id,
                    metric="agent.cost",
                    value=sum(costs),
                    idempotency_key=f"{result.run_id}:cost",
                )
            )
        return outcome_ids

    @staticmethod
    def _validate_guardrails(guardrails: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(guardrails, list):
            raise StoreError("guardrails must be an array")
        normalized = []
        for index, rule in enumerate(guardrails):
            if not isinstance(rule, dict) or not isinstance(rule.get("metric"), str):
                raise StoreError(f"guardrail {index} requires a metric")
            if rule.get("direction") not in {"max", "min"}:
                raise StoreError(f"guardrail {index} direction must be 'max' or 'min'")
            try:
                threshold = float(rule["threshold"])
                min_units = int(rule.get("min_units", 30))
            except (KeyError, TypeError, ValueError) as exc:
                raise StoreError(f"guardrail {index} requires numeric threshold and min_units") from exc
            if min_units < 1:
                raise StoreError(f"guardrail {index} min_units must be positive")
            if not math.isfinite(threshold):
                raise StoreError(f"guardrail {index} threshold must be finite")
            normalized.append(
                {
                    "metric": rule["metric"],
                    "direction": rule["direction"],
                    "threshold": threshold,
                    "min_units": min_units,
                }
            )
        return normalized

    def _pause_on_guardrail(self, name: str) -> None:
        status = self.status(name)
        if status["status"] != "running" or not any(item["violated"] for item in status["guardrails"]):
            return
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE experiments SET status='stopped',stopped_at=? WHERE name=? AND status='running'",
                (_now(), name),
            )
            if cursor.rowcount:
                self.store.record_audit(
                    connection,
                    action="experiment.guardrail_pause",
                    resource_type="experiment",
                    resource_id=name,
                    actor="system",
                    recorded_at=_now(),
                    details={"violations": [item for item in status["guardrails"] if item["violated"]]},
                )

    def stop(self, name: str) -> dict[str, Any]:
        current = self.store.query_all("SELECT status FROM experiments WHERE name=?", (name,))
        if not current:
            raise StoreError(f"unknown experiment {name!r}")
        if current[0]["status"] == "stopped":
            return self.status(name)
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE experiments SET status='stopped', stopped_at=? WHERE name=? AND status='running'",
                (_now(), name),
            )
            if cursor.rowcount != 1:
                raise StoreError(f"experiment {name!r} is not running")
            self.store.record_audit(
                connection,
                action="experiment.stop",
                resource_type="experiment",
                resource_id=name,
                actor=self.actor,
                recorded_at=_now(),
                details={},
            )
        return self.status(name)

    def promote(self, name: str, variation_key: str, *, environment: str = "prod") -> dict[str, Any]:
        with self.store.transaction() as connection:
            experiment = connection.execute("SELECT * FROM experiments WHERE name=?", (name,)).fetchone()
            if experiment is None:
                raise StoreError(f"unknown experiment {name!r}")
            variation = connection.execute(
                "SELECT * FROM variations WHERE experiment_id=? AND iteration=? AND variation_key=?",
                (experiment["experiment_id"], experiment["iteration"], variation_key),
            ).fetchone()
            if variation is None:
                raise StoreError(f"unknown variation {variation_key!r}")
            current = connection.execute(
                "SELECT plan_digest FROM deployments WHERE workflow_name=? AND environment=?",
                (experiment["workflow_name"], environment),
            ).fetchone()
            if experiment["status"] == "promoted":
                if (
                    experiment["promoted_variation"] == variation_key
                    and current
                    and current["plan_digest"] == variation["plan_digest"]
                ):
                    deployment = connection.execute(
                        "SELECT * FROM deployments WHERE workflow_name=? AND environment=?",
                        (experiment["workflow_name"], environment),
                    ).fetchone()
                    return dict(deployment)
                raise StoreError("a promoted experiment cannot promote another variation; create a new iteration")
            previous = current["plan_digest"] if current else None
            connection.execute(
                "INSERT INTO deployments VALUES(?,?,?,?,?) "
                "ON CONFLICT(workflow_name,environment) DO UPDATE SET "
                "previous_digest=deployments.plan_digest, plan_digest=excluded.plan_digest, "
                "updated_at=excluded.updated_at",
                (experiment["workflow_name"], environment, variation["plan_digest"], previous, _now()),
            )
            connection.execute(
                "UPDATE experiments SET status='promoted', stopped_at=?, promoted_variation=? WHERE experiment_id=?",
                (_now(), variation_key, experiment["experiment_id"]),
            )
            self.store.record_audit(
                connection,
                action="experiment.promote",
                resource_type="experiment",
                resource_id=name,
                actor=self.actor,
                recorded_at=_now(),
                details={
                    "variation": variation_key,
                    "environment": environment,
                    "plan_digest": variation["plan_digest"],
                },
            )
        return self.deployment(experiment["workflow_name"], environment)

    def rollback(self, workflow_name: str, *, environment: str = "prod") -> dict[str, Any]:
        with self.store.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM deployments WHERE workflow_name=? AND environment=?", (workflow_name, environment)
            ).fetchone()
            if current is None or current["previous_digest"] is None:
                raise StoreError(f"no rollback target for {workflow_name!r} in {environment!r}")
            connection.execute(
                "UPDATE deployments SET plan_digest=?, previous_digest=?, updated_at=? "
                "WHERE workflow_name=? AND environment=?",
                (current["previous_digest"], current["plan_digest"], _now(), workflow_name, environment),
            )
            self.store.record_audit(
                connection,
                action="rollback",
                resource_type="workflow",
                resource_id=workflow_name,
                actor=self.actor,
                recorded_at=_now(),
                details={"environment": environment, "from": current["plan_digest"], "to": current["previous_digest"]},
            )
        return self.deployment(workflow_name, environment)

    def deployment(self, workflow_name: str, environment: str) -> dict[str, Any]:
        rows = self.store.query_all(
            "SELECT * FROM deployments WHERE workflow_name=? AND environment=?", (workflow_name, environment)
        )
        if not rows:
            raise StoreError(f"no deployment for {workflow_name!r} in {environment!r}")
        return dict(rows[0])

    def status(self, name: str) -> dict[str, Any]:
        rows = self.store.query_all("SELECT * FROM experiments WHERE name=?", (name,))
        if not rows:
            raise StoreError(f"unknown experiment {name!r}")
        experiment = dict(rows[0])
        experiment["guardrails"] = json.loads(experiment.pop("guardrails_json"))
        variations = self.store.query_all(
            "SELECT v.variation_key,v.plan_digest,v.weight,"
            "COUNT(DISTINCT CASE WHEN e.exposure_id IS NOT NULL THEN a.assignment_id END) randomized_units,"
            "COUNT(DISTINCT e.exposure_id) exposures "
            "FROM variations v LEFT JOIN assignments a ON a.experiment_id=v.experiment_id "
            "AND a.variation_key=v.variation_key AND a.iteration=? "
            "LEFT JOIN exposures e ON e.assignment_id=a.assignment_id "
            "WHERE v.experiment_id=? AND v.iteration=? "
            "GROUP BY v.variation_key,v.plan_digest,v.weight ORDER BY v.variation_key",
            (experiment["iteration"], experiment["experiment_id"], experiment["iteration"]),
        )
        metrics = self.store.query_all(
            "SELECT variation_key,metric,COUNT(*) count,AVG(unit_value) mean,MIN(unit_value) min,MAX(unit_value) max "
            "FROM (SELECT a.assignment_id,a.variation_key,o.metric,AVG(o.value) unit_value "
            "FROM outcomes o JOIN assignments a ON a.assignment_id=o.assignment_id "
            "WHERE a.experiment_id=? AND a.iteration=? GROUP BY a.assignment_id,a.variation_key,o.metric) unit_metrics "
            "GROUP BY variation_key,metric",
            (experiment["experiment_id"], experiment["iteration"]),
        )
        variation_data = [dict(item) for item in variations]
        total = sum(item["randomized_units"] for item in variation_data)
        exposure_events = sum(item["exposures"] for item in variation_data)
        total_weight = sum(item["weight"] for item in variation_data)
        max_z = 0.0
        if total:
            for item in variation_data:
                probability = item["weight"] / total_weight
                variance = total * probability * (1 - probability)
                if variance > 0:
                    max_z = max(max_z, abs(item["randomized_units"] - total * probability) / math.sqrt(variance))
        health = "waiting" if total < 100 else ("sample_ratio_mismatch" if max_z >= 3.29 else "healthy")
        experiment.update(
            {
                "variations": variation_data,
                "metrics": [dict(item) for item in metrics],
                "health": {
                    "status": health,
                    "randomized_units": total,
                    "exposures": exposure_events,
                    "srm_max_z": round(max_z, 4),
                },
            }
        )
        metric_lookup = {(item["variation_key"], item["metric"]): item for item in experiment["metrics"]}
        evaluations = []
        for rule in experiment["guardrails"]:
            for variation in variation_data:
                observed = metric_lookup.get((variation["variation_key"], rule["metric"]))
                enough = observed is not None and observed["count"] >= rule["min_units"]
                violated = bool(
                    enough
                    and (
                        (rule["direction"] == "max" and observed["mean"] > rule["threshold"])
                        or (rule["direction"] == "min" and observed["mean"] < rule["threshold"])
                    )
                )
                evaluations.append(
                    {
                        **rule,
                        "variation_key": variation["variation_key"],
                        "count": observed["count"] if observed else 0,
                        "observed": observed["mean"] if observed else None,
                        "violated": violated,
                    }
                )
        experiment["guardrails"] = evaluations
        return experiment
