from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .codec import canonical_json
from .compiler import compile_workflow
from .errors import StoreError
from .model import ExecutionPlan
from .runtime import RunResult

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS workflow_versions (
    digest TEXT PRIMARY KEY,
    workflow_name TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(workflow_name, workflow_version)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    workflow_name TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    plan_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    error TEXT,
    experiment_json TEXT,
    FOREIGN KEY(plan_digest) REFERENCES workflow_versions(digest)
);
CREATE TABLE IF NOT EXISTS node_runs (
    span_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    node_hash TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    input_json TEXT,
    output_json TEXT,
    error TEXT,
    attributes_json TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    workflow_name TEXT NOT NULL,
    assignment_unit TEXT NOT NULL,
    primary_metric TEXT NOT NULL,
    guardrails_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK(status IN ('running','stopped','promoted')),
    iteration INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    stopped_at TEXT,
    promoted_variation TEXT
);
CREATE TABLE IF NOT EXISTS variations (
    experiment_id TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    variation_key TEXT NOT NULL,
    plan_digest TEXT NOT NULL,
    weight INTEGER NOT NULL CHECK(weight > 0),
    PRIMARY KEY(experiment_id, iteration, variation_key),
    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id),
    FOREIGN KEY(plan_digest) REFERENCES workflow_versions(digest)
);
CREATE TABLE IF NOT EXISTS assignments (
    assignment_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    unit_value TEXT NOT NULL,
    variation_key TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    UNIQUE(experiment_id, iteration, unit_value),
    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
);
CREATE TABLE IF NOT EXISTS exposures (
    exposure_id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE,
    exposed_at TEXT NOT NULL,
    FOREIGN KEY(assignment_id) REFERENCES assignments(assignment_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    FOREIGN KEY(assignment_id) REFERENCES assignments(assignment_id)
);
CREATE TABLE IF NOT EXISTS deployments (
    workflow_name TEXT NOT NULL,
    environment TEXT NOT NULL,
    plan_digest TEXT NOT NULL,
    previous_digest TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(workflow_name, environment),
    FOREIGN KEY(plan_digest) REFERENCES workflow_versions(digest)
);
CREATE TABLE IF NOT EXISTS audit_log (
    event_id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    details_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    run_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_runs_plan ON runs(plan_digest);
CREATE INDEX IF NOT EXISTS idx_exposures_assignment ON exposures(assignment_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_assignment_metric ON outcomes(assignment_id, metric);
"""


class Store:
    def __init__(self, path: str | Path = ".real/state.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            variation_columns = {row["name"] for row in connection.execute("PRAGMA table_info(variations)").fetchall()}
            if "iteration" not in variation_columns:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.executescript(
                    """
                    ALTER TABLE variations RENAME TO variations_legacy;
                    CREATE TABLE variations (
                        experiment_id TEXT NOT NULL,
                        iteration INTEGER NOT NULL,
                        variation_key TEXT NOT NULL,
                        plan_digest TEXT NOT NULL,
                        weight INTEGER NOT NULL CHECK(weight > 0),
                        PRIMARY KEY(experiment_id, iteration, variation_key),
                        FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id),
                        FOREIGN KEY(plan_digest) REFERENCES workflow_versions(digest)
                    );
                    INSERT INTO variations(experiment_id,iteration,variation_key,plan_digest,weight)
                    SELECT experiment_id,1,variation_key,plan_digest,weight FROM variations_legacy;
                    DROP TABLE variations_legacy;
                    """
                )
                connection.execute("PRAGMA foreign_keys = ON")
            experiment_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(experiments)").fetchall()
            }
            if "guardrails_json" not in experiment_columns:
                connection.execute("ALTER TABLE experiments ADD COLUMN guardrails_json TEXT NOT NULL DEFAULT '[]'")
            connection.execute("PRAGMA user_version = 3")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def register_plan(self, plan: ExecutionPlan) -> None:
        if plan.format_version != "0.1":
            raise StoreError(f"unsupported plan format_version {plan.format_version!r}")
        rebuilt = compile_workflow(plan.workflow, compiled_at=plan.compiled_at)
        if (
            rebuilt.digest != plan.digest
            or rebuilt.node_hashes != plan.node_hashes
            or rebuilt.prompt_hashes != plan.prompt_hashes
        ):
            raise StoreError("workflow plan failed integrity verification")
        payload = canonical_json(plan.to_dict())
        with self.transaction() as connection:
            dependencies = {
                node.config["plan_digest"]
                for node in plan.workflow.nodes
                if node.type == "subworkflow" and "plan_digest" in node.config
            }
            missing = [
                digest
                for digest in sorted(dependencies)
                if connection.execute("SELECT 1 FROM workflow_versions WHERE digest=?", (digest,)).fetchone() is None
            ]
            if missing:
                raise StoreError(f"subworkflow plan dependencies must be registered first: {missing}")
            existing = connection.execute(
                "SELECT digest, plan_json FROM workflow_versions WHERE workflow_name=? AND workflow_version=?",
                (plan.workflow.name, plan.workflow.version),
            ).fetchone()
            if existing and existing["digest"] != plan.digest:
                raise StoreError(
                    f"workflow {plan.workflow.name!r} version {plan.workflow.version!r} is immutable and already has "
                    f"digest {existing['digest']}"
                )
            connection.execute(
                "INSERT OR IGNORE INTO workflow_versions "
                "(digest,workflow_name,workflow_version,plan_json,created_at) VALUES(?,?,?,?,?)",
                (plan.digest, plan.workflow.name, plan.workflow.version, payload, plan.compiled_at),
            )

    def load_plan(self, digest: str) -> ExecutionPlan:
        with self._connect() as connection:
            row = connection.execute("SELECT plan_json FROM workflow_versions WHERE digest=?", (digest,)).fetchone()
        if row is None:
            raise StoreError(f"unknown workflow plan digest {digest!r}")
        plan = ExecutionPlan.from_dict(json.loads(row["plan_json"]))
        if plan.format_version != "0.1":
            raise StoreError(f"stored workflow plan {digest!r} uses an unsupported format version")
        rebuilt = compile_workflow(plan.workflow, compiled_at=plan.compiled_at)
        if rebuilt.digest != plan.digest:
            raise StoreError(f"stored workflow plan {digest!r} failed integrity verification")
        return plan

    def record_run(self, result: RunResult) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    result.run_id,
                    result.workflow_name,
                    result.workflow_version,
                    result.plan_digest,
                    result.status,
                    result.started_at,
                    result.ended_at,
                    result.duration_ms,
                    canonical_json(result.input if result.trace_input else None),
                    canonical_json(result.output if result.trace_output else None),
                    result.error,
                    canonical_json(result.experiment) if result.experiment else None,
                ),
            )
            for node in result.node_runs:
                connection.execute(
                    "INSERT INTO node_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        node.span_id,
                        result.run_id,
                        node.node_id,
                        node.node_type,
                        node.node_hash,
                        node.attempt,
                        node.status,
                        node.started_at,
                        node.ended_at,
                        node.duration_ms,
                        canonical_json(node.input),
                        canonical_json(node.output),
                        node.error,
                        canonical_json(node.attributes),
                    ),
                )

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            run = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                raise StoreError(f"unknown run {run_id!r}")
            spans = connection.execute(
                "SELECT * FROM node_runs WHERE run_id=? ORDER BY started_at, rowid", (run_id,)
            ).fetchall()
        result = dict(run)
        for key in ("input_json", "output_json", "experiment_json"):
            result[key.removesuffix("_json")] = json.loads(result.pop(key)) if result[key] is not None else None
        result["node_runs"] = []
        for span in spans:
            item = dict(span)
            for key in ("input_json", "output_json", "attributes_json"):
                item[key.removesuffix("_json")] = json.loads(item.pop(key)) if item[key] is not None else None
            result["node_runs"].append(item)
        return result

    def query_all(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return list(connection.execute(sql, parameters).fetchall())

    @staticmethod
    def record_audit(
        connection: sqlite3.Connection,
        *,
        action: str,
        resource_type: str,
        resource_id: str,
        actor: str,
        recorded_at: str,
        details: dict[str, Any],
    ) -> str:
        event_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO audit_log VALUES(?,?,?,?,?,?,?)",
            (event_id, action, resource_type, resource_id, actor, recorded_at, canonical_json(details)),
        )
        return event_id

    def list_audit(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise StoreError("audit limit must be 1..1000")
        rows = self.query_all("SELECT * FROM audit_log ORDER BY recorded_at DESC,rowid DESC LIMIT ?", (limit,))
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result

    def claim_idempotency(self, key: str, request_hash: str, *, created_at: str) -> dict[str, Any]:
        if not key or len(key) > 255:
            raise StoreError("Idempotency-Key must contain 1..255 characters")
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM idempotency_keys WHERE idempotency_key=?", (key,)).fetchone()
            if row is None:
                connection.execute("INSERT INTO idempotency_keys VALUES(?,?,NULL,?)", (key, request_hash, created_at))
                return {"status": "claimed"}
            if row["request_hash"] != request_hash:
                return {"status": "conflict"}
            if row["run_id"] is None:
                return {"status": "pending"}
            return {"status": "completed", "run_id": row["run_id"]}

    def finish_idempotency(self, key: str, run_id: str) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE idempotency_keys SET run_id=? WHERE idempotency_key=? AND run_id IS NULL",
                (run_id, key),
            )
            if cursor.rowcount != 1:
                raise StoreError("idempotency claim is missing or already completed")

    def release_idempotency(self, key: str) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM idempotency_keys WHERE idempotency_key=? AND run_id IS NULL", (key,))

    def deploy(
        self,
        plan: ExecutionPlan,
        *,
        environment: str,
        updated_at: str,
        actor: str = "system",
    ) -> dict[str, Any]:
        self.register_plan(plan)
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT plan_digest FROM deployments WHERE workflow_name=? AND environment=?",
                (plan.workflow.name, environment),
            ).fetchone()
            previous = current["plan_digest"] if current and current["plan_digest"] != plan.digest else None
            connection.execute(
                "INSERT INTO deployments VALUES(?,?,?,?,?) "
                "ON CONFLICT(workflow_name,environment) DO UPDATE SET "
                "previous_digest=CASE WHEN deployments.plan_digest<>excluded.plan_digest THEN deployments.plan_digest "
                "ELSE deployments.previous_digest END, plan_digest=excluded.plan_digest, "
                "updated_at=excluded.updated_at",
                (plan.workflow.name, environment, plan.digest, previous, updated_at),
            )
            row = connection.execute(
                "SELECT * FROM deployments WHERE workflow_name=? AND environment=?",
                (plan.workflow.name, environment),
            ).fetchone()
            self.record_audit(
                connection,
                action="deploy",
                resource_type="workflow",
                resource_id=plan.workflow.name,
                actor=actor,
                recorded_at=updated_at,
                details={"environment": environment, "plan_digest": plan.digest, "previous_digest": previous},
            )
        return dict(row)
