from __future__ import annotations

import hashlib
import json
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import AgentInfraError
from .model import ExecutionPlan
from .runtime import Runtime

Evaluator = Callable[[Any, Any, dict[str, Any]], float]


def exact_match(output: Any, expected: Any, _: dict[str, Any]) -> float:
    return 1.0 if output == expected else 0.0


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AgentInfraError(f"cannot read dataset {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AgentInfraError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(item, dict) or "input" not in item:
            raise AgentInfraError(f"dataset row {line_number} must be an object with an input property")
        cases.append(item)
    if not cases:
        raise AgentInfraError("evaluation dataset is empty")
    return cases


def evaluate(
    runtime: Runtime,
    plans: list[ExecutionPlan],
    cases: list[dict[str, Any]],
    *,
    evaluator: Evaluator = exact_match,
    evaluator_name: str = "exact_match",
    evaluator_version: str = "1",
) -> dict[str, Any]:
    results = []
    for plan in plans:
        case_results = []
        started = time.perf_counter()
        for index, case in enumerate(cases):
            run = runtime.run(plan, case["input"])
            score = 0.0
            if run.status == "succeeded":
                score = float(evaluator(run.output, case.get("expected"), case))
                if not 0 <= score <= 1:
                    raise AgentInfraError(f"evaluator score for case {case.get('id', index)!r} must be in [0, 1]")
            case_results.append(
                {
                    "case": case.get("id", index),
                    "run_id": run.run_id,
                    "status": run.status,
                    "score": score,
                    "output": run.output,
                    "expected": case.get("expected"),
                    "duration_ms": run.duration_ms,
                    "error": run.error,
                }
            )
        durations = [item["duration_ms"] for item in case_results]
        scores = [item["score"] for item in case_results]
        results.append(
            {
                "workflow": plan.workflow.name,
                "version": plan.workflow.version,
                "plan_digest": plan.digest,
                "cases": len(case_results),
                "mean_score": statistics.fmean(scores),
                "success_rate": sum(item["status"] == "succeeded" for item in case_results) / len(case_results),
                "mean_duration_ms": statistics.fmean(durations),
                "wall_duration_ms": (time.perf_counter() - started) * 1000,
                "results": case_results,
            }
        )
    ranked = sorted(results, key=lambda item: (-item["mean_score"], item["mean_duration_ms"]))
    dataset_digest = hashlib.sha256(
        json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "evaluator": {"name": evaluator_name, "version": evaluator_version},
        "dataset_size": len(cases),
        "dataset_digest": dataset_digest,
        "ranked_plan_digests": [x["plan_digest"] for x in ranked],
        "plans": results,
    }
