from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .codec import load_document, pretty_json, write_json
from .compiler import compile_workflow
from .diffing import diff_workflows
from .errors import AgentInfraError, ExecutionFailed, SpecError
from .evaluation import evaluate, load_jsonl
from .experiments import ExperimentManager
from .model import ExecutionPlan, WorkflowSpec
from .runtime import Runtime
from .schema import WORKFLOW_SCHEMA
from .store import Store
from .validation import validate_document


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="real", description="Define, run, observe, and experiment with workflows")
    parser.add_argument("--version", action="version", version="REAL Framework 0.1.0")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON (the stable default format)")
    parser.add_argument(
        "--db",
        default=os.environ.get("REAL_DB", os.environ.get("AGENT_INFRA_DB", ".real/state.db")),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create a minimal workflow project")
    init.add_argument("directory", nargs="?", default=".")
    init.add_argument("--name", default="my_workflow")
    init.add_argument("--dry-run", action="store_true")

    schema = commands.add_parser("schema", help="print the Workflow JSON Schema")
    schema.add_argument("--output", "-o")

    validate = commands.add_parser("validate", help="validate a workflow source")
    validate.add_argument("source")

    compile_command = commands.add_parser("compile", help="compile a source into an immutable plan")
    compile_command.add_argument("source")
    compile_command.add_argument("--output", "-o")
    compile_command.add_argument("--dry-run", action="store_true")

    diff = commands.add_parser("diff", help="show structural and content differences")
    diff.add_argument("before")
    diff.add_argument("after")

    run = commands.add_parser("run", help="execute a workflow or an assigned experiment variation")
    run.add_argument("source", nargs="?")
    run.add_argument("--input", required=True, help="JSON object or @path to a JSON file")
    run.add_argument("--tool", action="append", default=[], metavar="NAME=MODULE:CALLABLE[@VERSION]")
    run.add_argument("--provider", action="append", default=[], metavar="NAME=MODULE:CALLABLE[@VERSION]")
    run.add_argument("--adapters", help="JSON adapter registry for HTTP, MCP, and OpenAI-compatible endpoints")
    run.add_argument("--experiment")
    run.add_argument("--unit-name")
    run.add_argument("--unit-value")
    run.add_argument("--no-record", action="store_true")

    deploy = commands.add_parser("deploy", help="set an environment's active immutable plan")
    deploy.add_argument("source")
    deploy.add_argument("--environment", default="prod")
    deploy.add_argument("--dry-run", action="store_true")

    serve = commands.add_parser("serve", help="serve deployed and experimental workflows over HTTP")
    serve.add_argument("--plan", action="append", default=[], help="plan or workflow to register before serving")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--api-key-env", help="environment variable containing the bearer token")
    serve.add_argument("--max-concurrency", type=int, default=32)
    serve.add_argument("--tool", action="append", default=[], metavar="NAME=MODULE:CALLABLE[@VERSION]")
    serve.add_argument("--provider", action="append", default=[], metavar="NAME=MODULE:CALLABLE[@VERSION]")
    serve.add_argument("--adapters", help="JSON adapter registry")

    trace = commands.add_parser("trace", help="read one stored workflow trace")
    trace.add_argument("run_id")

    audit = commands.add_parser("audit", help="read control-plane mutation audit records")
    audit.add_argument("--limit", type=int, default=100)

    evaluation = commands.add_parser("eval", help="evaluate one or more plans over a JSONL dataset")
    evaluation.add_argument("sources", nargs="+")
    evaluation.add_argument("--dataset", required=True)
    evaluation.add_argument("--tool", action="append", default=[], metavar="NAME=MODULE:CALLABLE[@VERSION]")
    evaluation.add_argument("--provider", action="append", default=[], metavar="NAME=MODULE:CALLABLE[@VERSION]")
    evaluation.add_argument("--adapters", help="JSON adapter registry")
    evaluation.add_argument("--evaluator", metavar="NAME=MODULE:CALLABLE[@VERSION]")
    evaluation.add_argument("--output", "-o", help="write the complete evaluation artifact")

    canvas = commands.add_parser("canvas", help="edit the same workflow source as an interactive graph")
    canvas.add_argument("source")
    canvas.add_argument("--host", default="127.0.0.1")
    canvas.add_argument("--port", type=int, default=8765)
    canvas.add_argument("--open", action="store_true", dest="open_browser")

    experiment = commands.add_parser("experiment", help="manage online experiments")
    experiment_commands = experiment.add_subparsers(dest="experiment_command", required=True)
    start = experiment_commands.add_parser("start", help="start an experiment from a JSON config")
    start.add_argument("config")
    start.add_argument("--dry-run", action="store_true")
    iterate = experiment_commands.add_parser("iterate", help="start a new immutable iteration after stopping")
    iterate.add_argument("config")
    iterate.add_argument("--dry-run", action="store_true")
    status = experiment_commands.add_parser("status", help="show experiment traffic, health, and metrics")
    status.add_argument("name")
    stop = experiment_commands.add_parser("stop", help="stop assignment of new runs")
    stop.add_argument("name")
    stop.add_argument("--dry-run", action="store_true")
    promote = experiment_commands.add_parser("promote", help="promote a variation to an environment")
    promote.add_argument("name")
    promote.add_argument("variation")
    promote.add_argument("--environment", default="prod")
    promote.add_argument("--dry-run", action="store_true")

    outcome = commands.add_parser("outcome", help="attribute a delayed metric to an assignment")
    outcome.add_argument("assignment_id")
    outcome.add_argument("metric")
    outcome.add_argument("value", type=float)
    outcome.add_argument("--metadata", default="{}", help="JSON object")
    outcome.add_argument("--idempotency-key")

    rollback = commands.add_parser("rollback", help="swap a deployment back to its previous plan")
    rollback.add_argument("workflow")
    rollback.add_argument("--environment", default="prod")
    rollback.add_argument("--dry-run", action="store_true")
    return parser


def _load_plan(path: str) -> ExecutionPlan:
    value = load_document(path)
    if "workflow" in value and "digest" in value and "format_version" in value:
        issues = validate_document(value.get("workflow"))
        if issues:
            raise SpecError("invalid workflow in plan: " + "; ".join(f"{x.path}: {x.message}" for x in issues[:5]))
        plan = ExecutionPlan.from_dict(value)
        if plan.format_version != "0.1":
            raise SpecError(f"unsupported plan format_version {plan.format_version!r}")
        rebuilt = compile_workflow(plan.workflow, compiled_at=plan.compiled_at)
        if (
            rebuilt.digest != plan.digest
            or rebuilt.node_hashes != plan.node_hashes
            or rebuilt.prompt_hashes != plan.prompt_hashes
        ):
            raise SpecError(f"compiled plan {path} failed digest verification")
        return plan
    issues = validate_document(value)
    if issues:
        raise SpecError("invalid workflow: " + "; ".join(f"{x.path}: {x.message}" for x in issues[:5]))
    return compile_workflow(WorkflowSpec.from_dict(value))


def _load_input(argument: str) -> dict[str, Any]:
    if argument.startswith("@"):
        value = load_document(argument[1:])
    else:
        try:
            value = json.loads(argument)
        except json.JSONDecodeError as exc:
            raise SpecError(f"invalid --input JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SpecError("workflow input must be a JSON object")
    return value


def _load_callable(spec: str) -> tuple[str, Callable[..., Any], str]:
    try:
        name, location = spec.split("=", 1)
        target, separator, version = location.rpartition("@")
        if not separator:
            target, version = location, "unversioned"
        module_name, callable_name = target.split(":", 1)
        function = getattr(importlib.import_module(module_name), callable_name)
    except (ValueError, ImportError, AttributeError) as exc:
        raise SpecError(f"invalid adapter {spec!r}; expected NAME=MODULE:CALLABLE[@VERSION]: {exc}") from exc
    if not name or not callable(function):
        raise SpecError(f"invalid adapter {spec!r}")
    return name, function, version


def _runtime(args: argparse.Namespace, store: Store | None) -> Runtime:
    runtime = Runtime(trace_sink=store, plan_loader=store.load_plan if store is not None else None)
    if getattr(args, "adapters", None):
        _configure_adapters(runtime, args.adapters)
    for item in getattr(args, "tool", []):
        name, function, version = _load_callable(item)
        runtime.register_tool(name, function, version=version)
    for item in getattr(args, "provider", []):
        name, function, version = _load_callable(item)
        runtime.register_provider(name, function, version=version)
    return runtime


def _adapter_headers(config: dict[str, Any]) -> dict[str, str]:
    headers = config.get("headers", {})
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise SpecError("adapter headers must be a string-to-string object")
    result = dict(headers)
    environment_headers = config.get("headers_env", {})
    if not isinstance(environment_headers, dict):
        raise SpecError("adapter headers_env must be an object mapping header names to environment variables")
    for header, variable in environment_headers.items():
        value = os.environ.get(variable)
        if not value:
            raise SpecError(f"adapter header environment variable {variable!r} is empty or missing")
        result[header] = value
    return result


def _configure_adapters(runtime: Runtime, path: str) -> None:
    from .adapters import HTTPJSONTool, MCPStreamableHTTPTool, OpenAICompatibleProvider

    document = load_document(path)
    for name, config in document.get("tools", {}).items():
        if not isinstance(config, dict):
            raise SpecError(f"tool adapter {name!r} must be an object")
        version = config.get("version")
        allowed_hosts = tuple(config.get("allowed_hosts", []))
        if not version or not allowed_hosts:
            raise SpecError(f"tool adapter {name!r} requires version and a non-empty allowed_hosts array")
        common = {
            "url": config.get("url", ""),
            "headers": _adapter_headers(config),
            "timeout": float(config.get("timeout", 30)),
            "allowed_hosts": allowed_hosts,
        }
        if config.get("type") == "http-json":
            adapter = HTTPJSONTool(**common)
        elif config.get("type") == "mcp":
            adapter = MCPStreamableHTTPTool(
                **common,
                tool_name=config.get("tool_name", name),
                protocol_version=config.get("protocol_version", "2025-06-18"),
            )
        else:
            raise SpecError(f"unknown tool adapter type for {name!r}")
        runtime.register_tool(name, adapter, version=version)
    for name, config in document.get("providers", {}).items():
        if not isinstance(config, dict) or config.get("type") != "openai-compatible":
            raise SpecError(f"provider adapter {name!r} must have type 'openai-compatible'")
        version = config.get("version")
        allowed_hosts = tuple(config.get("allowed_hosts", []))
        if not version or not allowed_hosts:
            raise SpecError(f"provider adapter {name!r} requires version and a non-empty allowed_hosts array")
        adapter = OpenAICompatibleProvider(
            base_url=config.get("base_url", ""),
            api_key_env=config.get("api_key_env"),
            headers=_adapter_headers(config),
            timeout=float(config.get("timeout", 60)),
            allowed_hosts=allowed_hosts,
        )
        runtime.register_provider(name, adapter, version=version)


def _init_project(directory: Path, name: str, dry_run: bool) -> dict[str, Any]:
    workflow = {
        "spec_version": "0.1",
        "name": name,
        "version": "1.0.0",
        "description": "A deterministic starter workflow",
        "input_schema": {
            "type": "object",
            "required": ["message"],
            "properties": {"message": {"type": "string"}},
            "additionalProperties": False,
        },
        "output_schema": {"type": "object"},
        "entry": "render",
        "nodes": [
            {"id": "render", "type": "template", "config": {"template": "Received: ${$.input.message}"}},
            {"id": "result", "type": "output", "config": {"value": {"message": "${$.nodes.render}"}}},
        ],
        "edges": [{"source": "render", "target": "result"}],
    }
    files = [str(directory / "workflow.json"), str(directory / "dataset.jsonl")]
    if not dry_run:
        directory.mkdir(parents=True, exist_ok=True)
        workflow_path = directory / "workflow.json"
        dataset_path = directory / "dataset.jsonl"
        if workflow_path.exists() or dataset_path.exists():
            raise SpecError("init refuses to overwrite workflow.json or dataset.jsonl")
        write_json(workflow_path, workflow)
        dataset_path.write_text(
            json.dumps({"id": "hello", "input": {"message": "hello"}, "expected": {"message": "Received: hello"}})
            + "\n",
            encoding="utf-8",
        )
    return {"ok": True, "dry_run": dry_run, "files": files, "next": "agent validate workflow.json"}


def _experiment_start(config_path: str, manager: ExperimentManager, dry_run: bool) -> dict[str, Any]:
    config = load_document(config_path)
    variations = []
    base = Path(config_path).resolve().parent
    for item in config.get("variations", []):
        if not isinstance(item, dict) or not all(key in item for key in ("key", "source")):
            raise SpecError("each experiment variation requires key and source")
        source = Path(item["source"])
        if not source.is_absolute():
            source = base / source
        variations.append((item["key"], _load_plan(str(source)), int(item.get("weight", 1))))
    proposal = {
        "name": config.get("name"),
        "assignment_unit": config.get("assignment_unit"),
        "primary_metric": config.get("primary_metric"),
        "guardrails": config.get("guardrails", []),
        "variations": [{"key": key, "plan_digest": plan.digest, "weight": weight} for key, plan, weight in variations],
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "proposal": proposal}
    return manager.start(
        name=config.get("name", ""),
        assignment_unit=config.get("assignment_unit", ""),
        primary_metric=config.get("primary_metric", ""),
        variations=variations,
        guardrails=config.get("guardrails", []),
    )


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "init":
        return _init_project(Path(args.directory), args.name, args.dry_run)
    if args.command == "schema":
        if args.output:
            write_json(args.output, WORKFLOW_SCHEMA)
        return WORKFLOW_SCHEMA
    if args.command == "validate":
        issues = validate_document(load_document(args.source))
        return {"ok": not issues, "source": args.source, "issues": [item.to_dict() for item in issues]}
    if args.command == "compile":
        plan = _load_plan(args.source)
        output = args.output or str(Path(args.source).with_suffix(".plan.json"))
        if not args.dry_run:
            store = Store(args.db)
            store.register_plan(plan)
            write_json(output, plan.to_dict())
        return {"ok": True, "dry_run": args.dry_run, "output": output, "plan": plan.to_dict()}
    if args.command == "diff":
        return diff_workflows(_load_plan(args.before).workflow, _load_plan(args.after).workflow)

    store = Store(args.db)
    actor = os.environ.get("REAL_ACTOR", os.environ.get("AGENT_INFRA_ACTOR", "local-cli"))
    manager = ExperimentManager(store, actor=actor)
    if args.command == "run":
        assignment = None
        if args.experiment:
            if not args.unit_name or args.unit_value is None:
                raise SpecError("--experiment requires --unit-name and --unit-value")
            assignment = manager.assign(args.experiment, unit_name=args.unit_name, unit_value=args.unit_value)
            plan = store.load_plan(assignment.plan_digest)
        else:
            if not args.source:
                raise SpecError("run requires SOURCE unless --experiment is used")
            plan = _load_plan(args.source)
        if args.no_record and assignment is not None:
            raise SpecError("experiment runs must be recorded so exposures remain attributable")
        if not args.no_record:
            store.register_plan(plan)
        runtime = _runtime(args, None if args.no_record else store)
        result = runtime.run(
            plan,
            _load_input(args.input),
            experiment=assignment.lineage() if assignment else None,
        )
        if assignment is not None:
            manager.expose(assignment, result.run_id)
            manager.record_run_metrics(assignment, result)
        payload = result.to_dict()
        if assignment is not None:
            payload["exposure_recorded"] = True
        if result.status != "succeeded":
            raise ExecutionFailed(result.error or "workflow execution failed")
        return payload
    if args.command == "deploy":
        plan = _load_plan(args.source)
        if args.dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "workflow_name": plan.workflow.name,
                "environment": args.environment,
                "plan_digest": plan.digest,
            }
        return store.deploy(
            plan,
            environment=args.environment,
            updated_at=datetime.now(timezone.utc).isoformat(),
            actor=actor,
        )
    if args.command == "trace":
        return store.get_run(args.run_id)
    if args.command == "audit":
        return {"events": store.list_audit(limit=args.limit)}
    if args.command == "eval":
        runtime = _runtime(args, store)
        plans = [_load_plan(source) for source in args.sources]
        for plan in plans:
            store.register_plan(plan)
        evaluator = None
        evaluator_name = "exact_match"
        evaluator_version = "1"
        if args.evaluator:
            evaluator_name, evaluator, evaluator_version = _load_callable(args.evaluator)
        result = evaluate(
            runtime,
            plans,
            load_jsonl(args.dataset),
            **({"evaluator": evaluator} if evaluator is not None else {}),
            evaluator_name=evaluator_name,
            evaluator_version=evaluator_version,
        )
        if args.output:
            write_json(args.output, result)
        return result
    if args.command == "outcome":
        try:
            metadata = json.loads(args.metadata)
        except json.JSONDecodeError as exc:
            raise SpecError(f"invalid --metadata JSON: {exc}") from exc
        if not isinstance(metadata, dict):
            raise SpecError("--metadata must be a JSON object")
        return {
            "ok": True,
            "outcome_id": manager.outcome(
                args.assignment_id,
                metric=args.metric,
                value=args.value,
                metadata=metadata,
                idempotency_key=args.idempotency_key,
            ),
        }
    if args.command == "rollback":
        if args.dry_run:
            current = manager.deployment(args.workflow, args.environment)
            return {"ok": True, "dry_run": True, "current": current, "target": current["previous_digest"]}
        return manager.rollback(args.workflow, environment=args.environment)
    if args.command == "experiment":
        if args.experiment_command == "start":
            return _experiment_start(args.config, manager, args.dry_run)
        if args.experiment_command == "iterate":
            config = load_document(args.config)
            base = Path(args.config).resolve().parent
            variations = []
            for item in config.get("variations", []):
                if not isinstance(item, dict) or not all(key in item for key in ("key", "source")):
                    raise SpecError("each experiment variation requires key and source")
                source = Path(item["source"])
                if not source.is_absolute():
                    source = base / source
                variations.append((item["key"], _load_plan(str(source)), int(item.get("weight", 1))))
            if args.dry_run:
                return {
                    "ok": True,
                    "dry_run": True,
                    "name": config.get("name"),
                    "variations": [
                        {"key": key, "plan_digest": plan.digest, "weight": weight} for key, plan, weight in variations
                    ],
                }
            return manager.iterate(config.get("name", ""), variations, guardrails=config.get("guardrails"))
        if args.experiment_command == "status":
            return manager.status(args.name)
        if args.experiment_command == "stop":
            if args.dry_run:
                return {"ok": True, "dry_run": True, "current": manager.status(args.name)}
            return manager.stop(args.name)
        if args.experiment_command == "promote":
            if args.dry_run:
                status = manager.status(args.name)
                target = next((item for item in status["variations"] if item["variation_key"] == args.variation), None)
                if target is None:
                    raise SpecError(f"unknown variation {args.variation!r}")
                return {"ok": True, "dry_run": True, "target": target, "environment": args.environment}
            return manager.promote(args.name, args.variation, environment=args.environment)
    raise SpecError(f"unknown command {args.command!r}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "canvas":
            from .canvas import CanvasServer

            issues = validate_document(load_document(args.source))
            if issues:
                raise SpecError(
                    "Canvas source is invalid: " + "; ".join(f"{item.path}: {item.message}" for item in issues[:5])
                )
            server = CanvasServer(args.source, args.host, args.port)
            sys.stdout.write(pretty_json({"ok": True, "url": server.url, "source": str(Path(args.source).resolve())}))
            sys.stdout.flush()
            server.serve(open_browser=args.open_browser)
            return 0
        if args.command == "serve":
            from .server import AgentServer

            store = Store(args.db)
            for source in args.plan:
                store.register_plan(_load_plan(source))
            api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
            if args.api_key_env and not api_key:
                raise SpecError(f"API key environment variable {args.api_key_env!r} is empty or missing")
            server = AgentServer(
                store,
                _runtime(args, store),
                host=args.host,
                port=args.port,
                api_key=api_key,
                max_concurrency=args.max_concurrency,
            )
            sys.stdout.write(pretty_json({"ok": True, "url": server.url, "database": str(Path(args.db).resolve())}))
            sys.stdout.flush()
            server.serve()
            return 0
        result = execute(args)
        sys.stdout.write(pretty_json(result))
        if args.command == "validate" and not result["ok"]:
            return 2
        return 0
    except AgentInfraError as exc:
        sys.stderr.write(pretty_json({"ok": False, "error": type(exc).__name__, "message": str(exc)}))
        return 3 if isinstance(exc, ExecutionFailed) else 2
    except KeyboardInterrupt:
        sys.stderr.write(pretty_json({"ok": False, "error": "Interrupted", "message": "interrupted"}))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
