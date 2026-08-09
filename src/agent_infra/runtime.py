from __future__ import annotations

import asyncio
import copy
import inspect
import json
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .compiler import compile_workflow
from .errors import ExecutionFailed
from .model import EdgeSpec, ExecutionPlan, NodeSpec
from .validation import validate_json_value

Tool = Callable[[dict[str, Any]], Any | Awaitable[Any]]
ModelProvider = Callable[[dict[str, Any]], Any | Awaitable[Any]]
PlanLoader = Callable[[str], ExecutionPlan]
TOKEN = re.compile(r"\$\{(\$\.[^}]+)\}")
NO_OUTPUT = object()


class TraceSink(Protocol):
    def record_run(self, result: RunResult) -> None: ...


class CompositeTraceSink:
    def __init__(self, *sinks: TraceSink) -> None:
        self.sinks = sinks

    def record_run(self, result: RunResult) -> None:
        for sink in self.sinks:
            sink.record_run(result)


@dataclass(frozen=True)
class RegisteredTool:
    function: Tool
    version: str


@dataclass(frozen=True)
class RegisteredProvider:
    function: ModelProvider
    version: str


@dataclass
class NodeRun:
    span_id: str
    node_id: str
    node_type: str
    node_hash: str
    attempt: int
    started_at: str
    ended_at: str
    duration_ms: float
    status: str
    input: Any = None
    output: Any = None
    error: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "node_hash": self.node_hash,
            "attempt": self.attempt,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "input": self.input,
            "output": self.output,
            "error": self.error,
            "attributes": self.attributes,
        }


@dataclass
class RunResult:
    run_id: str
    workflow_name: str
    workflow_version: str
    plan_digest: str
    started_at: str
    ended_at: str
    duration_ms: float
    status: str
    input: Any
    output: Any
    node_runs: list[NodeRun]
    error: str | None = None
    experiment: dict[str, Any] | None = None
    trace_input: bool = True
    trace_output: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_name": self.workflow_name,
            "workflow_version": self.workflow_version,
            "plan_digest": self.plan_digest,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "input": self.input,
            "output": self.output,
            "error": self.error,
            "experiment": self.experiment,
            "node_runs": [node.to_dict() for node in self.node_runs],
        }


class Runtime:
    """Execute immutable plans in one process with explicit tool/provider registries."""

    def __init__(self, *, trace_sink: TraceSink | None = None, plan_loader: PlanLoader | None = None) -> None:
        self.tools: dict[str, RegisteredTool] = {}
        self.providers: dict[str, RegisteredProvider] = {}
        self.trace_sink = trace_sink
        self.plan_loader = plan_loader
        self._plan_cache: dict[str, tuple[ExecutionPlan, dict[str, NodeSpec], dict[str, list[EdgeSpec]]]] = {}
        self._plan_cache_lock = threading.Lock()

    def register_tool(self, name: str, function: Tool, *, version: str = "unversioned") -> Runtime:
        self.tools[name] = RegisteredTool(function, version)
        return self

    def register_provider(self, name: str, function: ModelProvider, *, version: str = "unversioned") -> Runtime:
        self.providers[name] = RegisteredProvider(function, version)
        return self

    def run(
        self,
        plan: ExecutionPlan,
        input_value: dict[str, Any],
        *,
        run_id: str | None = None,
        experiment: dict[str, Any] | None = None,
    ) -> RunResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.arun(plan, input_value, run_id=run_id, experiment=experiment))
        raise ExecutionFailed("Runtime.run cannot be called inside an event loop; use await Runtime.arun")

    async def arun(
        self,
        plan: ExecutionPlan,
        input_value: dict[str, Any],
        *,
        run_id: str | None = None,
        experiment: dict[str, Any] | None = None,
    ) -> RunResult:
        plan, nodes, outgoing = self._prepare_plan(plan)
        input_issues = validate_json_value(input_value, plan.workflow.input_schema)
        if input_issues:
            detail = "; ".join(f"{item.path}: {item.message}" for item in input_issues)
            raise ExecutionFailed(f"workflow input does not match input_schema: {detail}")

        identifier = run_id or uuid.uuid4().hex
        started_wall = _now()
        started = time.perf_counter()
        node_runs: list[NodeRun] = []
        state: dict[str, Any] = {"input": copy.deepcopy(input_value), "nodes": {}, "vars": {}}
        final_output: Any = NO_OUTPUT
        status = "succeeded"
        error: str | None = None
        edge_counts: dict[tuple[str, str], int] = {}
        execution_counts: dict[str, int] = {}
        completed: set[str] = set()
        frontier = [plan.workflow.entry]
        deferred: list[str] = []

        try:
            while frontier or deferred:
                candidates = list(dict.fromkeys(frontier + deferred))
                frontier = []
                deferred = []
                ready: list[str] = []
                for node_id in candidates:
                    node = nodes[node_id]
                    wait_for = node.config.get("wait_for", []) if node.type == "join" else []
                    if all(item in completed for item in wait_for):
                        ready.append(node_id)
                    else:
                        deferred.append(node_id)
                if not ready:
                    raise ExecutionFailed(f"workflow is stalled waiting for join dependencies: {deferred}")

                results = await asyncio.gather(
                    *[
                        self._execute_with_retry(
                            node=nodes[node_id],
                            state=state,
                            node_hash=plan.node_hashes[node_id],
                            prompt_hash=plan.prompt_hashes.get(node_id),
                            node_runs=node_runs,
                        )
                        for node_id in ready
                    ]
                )
                for node_id, value in zip(ready, results, strict=True):
                    node = nodes[node_id]
                    execution_counts[node_id] = execution_counts.get(node_id, 0) + 1
                    state["nodes"][node_id] = value
                    save_as = node.config.get("save_as")
                    if save_as:
                        state["vars"][save_as] = value
                    completed.add(node_id)
                    if node.type == "output":
                        final_output = value
                    for edge in outgoing[node_id]:
                        if not _condition_matches(edge.when, state):
                            continue
                        edge_key = (edge.source, edge.target)
                        count = edge_counts.get(edge_key, 0)
                        if edge.max_iterations is not None and count >= edge.max_iterations:
                            continue
                        is_repeat = edge.target in completed
                        if is_repeat and edge.max_iterations is None:
                            continue
                        edge_counts[edge_key] = count + 1
                        frontier.append(edge.target)

            if final_output is NO_OUTPUT:
                raise ExecutionFailed("workflow completed without reaching an output node")
            output_issues = validate_json_value(final_output, plan.workflow.output_schema)
            if output_issues:
                detail = "; ".join(f"{item.path}: {item.message}" for item in output_issues)
                raise ExecutionFailed(f"workflow output does not match output_schema: {detail}")
        except Exception as exc:
            status = "failed"
            error = str(exc)

        ended = time.perf_counter()
        runtime_metadata = plan.workflow.metadata.get("runtime", {})
        if not isinstance(runtime_metadata, dict):
            runtime_metadata = {}
        result = RunResult(
            run_id=identifier,
            workflow_name=plan.workflow.name,
            workflow_version=plan.workflow.version,
            plan_digest=plan.digest,
            started_at=started_wall,
            ended_at=_now(),
            duration_ms=(ended - started) * 1000,
            status=status,
            input=input_value,
            output=None if final_output is NO_OUTPUT else final_output,
            node_runs=node_runs,
            error=error,
            experiment=experiment,
            trace_input=runtime_metadata.get("trace_input", True),
            trace_output=runtime_metadata.get("trace_output", True),
        )
        if self.trace_sink is not None:
            self.trace_sink.record_run(result)
        return result

    def _prepare_plan(
        self, plan: ExecutionPlan
    ) -> tuple[ExecutionPlan, dict[str, NodeSpec], dict[str, list[EdgeSpec]]]:
        with self._plan_cache_lock:
            cached = self._plan_cache.get(plan.digest)
            if cached is not None:
                return cached
            rebuilt = compile_workflow(plan.workflow, compiled_at=plan.compiled_at)
            if (
                plan.format_version != "0.1"
                or rebuilt.digest != plan.digest
                or rebuilt.node_hashes != plan.node_hashes
                or rebuilt.prompt_hashes != plan.prompt_hashes
            ):
                raise ExecutionFailed("workflow plan failed integrity verification")
            safe_plan = copy.deepcopy(plan)
            nodes = {node.id: node for node in safe_plan.workflow.nodes}
            outgoing: dict[str, list[EdgeSpec]] = {node.id: [] for node in safe_plan.workflow.nodes}
            for edge in safe_plan.workflow.edges:
                outgoing[edge.source].append(edge)
            prepared = (safe_plan, nodes, outgoing)
            self._plan_cache[plan.digest] = prepared
            return prepared

    async def _execute_with_retry(
        self,
        *,
        node: NodeSpec,
        state: dict[str, Any],
        node_hash: str,
        prompt_hash: str | None,
        node_runs: list[NodeRun],
    ) -> Any:
        attempts = node.retry + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            started_wall = _now()
            started = time.perf_counter()
            node_input = _resolve(node.config, state)
            attributes: dict[str, Any] = {}
            if prompt_hash is not None:
                attributes["agent_infra.prompt.hash"] = prompt_hash
            try:
                operation = self._execute_node(node, state, attributes)
                if node.timeout_ms is not None:
                    value = await asyncio.wait_for(operation, timeout=node.timeout_ms / 1000)
                else:
                    value = await operation
                ended = time.perf_counter()
                node_runs.append(
                    NodeRun(
                        span_id=uuid.uuid4().hex,
                        node_id=node.id,
                        node_type=node.type,
                        node_hash=node_hash,
                        attempt=attempt,
                        started_at=started_wall,
                        ended_at=_now(),
                        duration_ms=(ended - started) * 1000,
                        status="succeeded",
                        input=node_input if node.config.get("trace_content", True) else None,
                        output=value if node.config.get("trace_content", True) else None,
                        attributes=attributes,
                    )
                )
                return value
            except Exception as exc:
                last_error = exc
                ended = time.perf_counter()
                node_runs.append(
                    NodeRun(
                        span_id=uuid.uuid4().hex,
                        node_id=node.id,
                        node_type=node.type,
                        node_hash=node_hash,
                        attempt=attempt,
                        started_at=started_wall,
                        ended_at=_now(),
                        duration_ms=(ended - started) * 1000,
                        status="failed",
                        input=node_input if node.config.get("trace_content", True) else None,
                        error=str(exc),
                        attributes=attributes,
                    )
                )
        assert last_error is not None
        raise ExecutionFailed(f"node {node.id!r} failed after {attempts} attempt(s): {last_error}") from last_error

    async def _execute_node(self, node: NodeSpec, state: dict[str, Any], attributes: dict[str, Any]) -> Any:
        config = node.config
        if node.type == "constant":
            return _resolve(config["value"], state)
        if node.type == "template":
            return _resolve(config["template"], state)
        if node.type == "passthrough":
            return _resolve(config.get("value", "${$.input}"), state)
        if node.type == "branch":
            return _resolve(config["value"], state)
        if node.type == "join":
            return {name: copy.deepcopy(state["nodes"].get(name)) for name in config["wait_for"]}
        if node.type == "output":
            return _resolve(config["value"], state)
        if node.type == "subworkflow":
            if self.plan_loader is None:
                raise ExecutionFailed("subworkflow execution requires a plan_loader")
            child_plan = self.plan_loader(config["plan_digest"])
            child_input = _resolve(config["input"], state)
            if not isinstance(child_input, dict):
                raise ExecutionFailed("subworkflow input must resolve to an object")
            child = await self.arun(child_plan, child_input)
            attributes.update(
                {
                    "agent_infra.subworkflow.run_id": child.run_id,
                    "agent_infra.subworkflow.plan_digest": child.plan_digest,
                    "agent_infra.subworkflow.version": child.workflow_version,
                }
            )
            if child.status != "succeeded":
                raise ExecutionFailed(f"subworkflow {child.workflow_name!r} failed: {child.error}")
            return child.output
        if node.type == "tool":
            name = config["tool"]
            registered = self.tools.get(name)
            if registered is None:
                raise ExecutionFailed(f"tool {name!r} is not registered")
            if registered.version != config["tool_version"]:
                raise ExecutionFailed(
                    f"tool {name!r} version mismatch: plan requires {config['tool_version']!r}, "
                    f"runtime registered {registered.version!r}"
                )
            attributes.update(
                {
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": name,
                    "agent_infra.tool.version": registered.version,
                }
            )
            arguments = _resolve(config.get("arguments", {}), state)
            return await _invoke(registered.function, arguments)
        if node.type == "llm":
            name = config["provider"]
            registered = self.providers.get(name)
            if registered is None:
                raise ExecutionFailed(f"model provider {name!r} is not registered")
            if registered.version != config["provider_version"]:
                raise ExecutionFailed(
                    f"provider {name!r} version mismatch: plan requires {config['provider_version']!r}, "
                    f"runtime registered {registered.version!r}"
                )
            request = {
                "model": config["model"],
                "model_version": config["model_version"],
                "prompt": _resolve(config["prompt"], state),
                "system": _resolve(config.get("system"), state),
                "parameters": _resolve(config.get("parameters", {}), state),
            }
            attributes.update(
                {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.provider.name": name,
                    "gen_ai.request.model": config["model"],
                    "agent_infra.model.version": config["model_version"],
                    "agent_infra.provider.version": registered.version,
                }
            )
            response = await _invoke(registered.function, request)
            if isinstance(response, dict) and "output" in response:
                attribute_names = {
                    "input_tokens": "gen_ai.usage.input_tokens",
                    "output_tokens": "gen_ai.usage.output_tokens",
                    "response_model": "gen_ai.response.model",
                    "cost": "gen_ai.usage.cost",
                }
                for key, attribute_name in attribute_names.items():
                    if response.get(key) is not None:
                        attributes[attribute_name] = response[key]
                response_model = response.get("response_model")
                if (
                    response_model is not None
                    and response_model != config["model_version"]
                    and not config.get("allow_model_version_mismatch", False)
                ):
                    raise ExecutionFailed(
                        f"model version mismatch: plan requires {config['model_version']!r}, "
                        f"provider returned {response_model!r}"
                    )
                return response["output"]
            return response
        raise ExecutionFailed(f"unsupported node type {node.type!r}")


async def _invoke(function: Callable[[dict[str, Any]], Any], argument: dict[str, Any]) -> Any:
    call = function.__call__ if not inspect.isfunction(function) and callable(function) else function
    if inspect.iscoroutinefunction(call):
        return await function(argument)
    result = await asyncio.to_thread(function, argument)
    return await result if inspect.isawaitable(result) else result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_path(path: str, state: dict[str, Any]) -> Any:
    if path == "$":
        return state
    if not path.startswith("$."):
        raise ExecutionFailed(f"value reference must start with '$.': {path!r}")
    current: Any = state
    for part in path[2:].split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise ExecutionFailed(f"value reference {path!r} does not exist")
    return copy.deepcopy(current)


def _resolve(value: Any, state: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve(item, state) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, state) for item in value]
    if not isinstance(value, str):
        return copy.deepcopy(value)
    match = TOKEN.fullmatch(value)
    if match:
        return _get_path(match.group(1), state)

    def replace(token: re.Match[str]) -> str:
        resolved = _get_path(token.group(1), state)
        return resolved if isinstance(resolved, str) else json.dumps(resolved, ensure_ascii=False, sort_keys=True)

    return TOKEN.sub(replace, value)


def _condition_matches(condition: dict[str, Any] | None, state: dict[str, Any]) -> bool:
    if condition is None:
        return True
    try:
        actual = _get_path(condition["path"], state)
    except ExecutionFailed:
        actual = None
    op = condition["op"]
    expected = condition.get("value")
    if op == "exists":
        return actual is not None
    if op == "truthy":
        return bool(actual)
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "gt":
        return actual > expected
    if op == "gte":
        return actual >= expected
    if op == "lt":
        return actual < expected
    if op == "lte":
        return actual <= expected
    if op == "in":
        return actual in expected
    if op == "contains":
        return expected in actual
    return False
