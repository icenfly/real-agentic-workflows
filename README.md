# REAL Framework

**R**eproducible **E**xperimentation for **A**gentic **L**ogic.

[![CI](https://github.com/icenfly/real-agentic-workflows/actions/workflows/ci.yml/badge.svg)](https://github.com/icenfly/real-agentic-workflows/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

[简体中文](README.zh-CN.md)

REAL is an open-source framework for building, observing, evaluating, and safely experimenting with versioned agentic workflows. It is designed for repeatable business tasks where reproducibility and real online outcomes matter more than open-ended multi-agent conversation.

## Why REAL?

- **One source of truth:** JSON or optional YAML powers the CLI, SDK, Canvas, compiler, and runtime.
- **Reproducible execution:** immutable plans carry workflow, node, prompt, model, provider, and tool lineage.
- **Real experimentation:** stable business-unit assignment, execution-backed exposure, delayed outcomes, SRM checks, guardrails, promotion, and rollback.
- **Observable by default:** run and node traces, latency, token/cost attributes, content redaction, SQLite storage, and optional OpenTelemetry export.
- **Portable integrations:** explicit tool/model registries plus HTTP JSON, MCP Streamable HTTP, and OpenAI-compatible adapters.
- **Agent-friendly operations:** a structured non-interactive CLI and repository skill for Codex or Claude Code.

## Install

From source:

```bash
git clone https://github.com/icenfly/real-agentic-workflows.git
cd real-agentic-workflows
python -m venv .venv
source .venv/bin/activate
pip install -e '.[yaml,otel]'
```

The distribution name is `real-agentic-workflows`. The primary command is `real`; `agent` remains a compatibility alias. The Python import namespace is currently `agent_infra`.

## Build and run a workflow

```bash
real init customer-triage --name customer_triage
cd customer-triage

real validate workflow.json
real compile workflow.json
real run workflow.plan.json --input '{"message":"refund requested"}'
real eval workflow.plan.json --dataset dataset.jsonl
real canvas workflow.json
```

Every successful CLI response is JSON. Errors go to stderr with non-zero exit codes. Mutating control-plane commands support `--dry-run` where applicable.

A workflow is plain data:

```json
{
  "spec_version": "0.1",
  "name": "hello",
  "version": "1.0.0",
  "input_schema": {
    "type": "object",
    "required": ["message"]
  },
  "output_schema": {"type": "object"},
  "entry": "render",
  "nodes": [
    {
      "id": "render",
      "type": "template",
      "config": {"template": "Received: ${$.input.message}"}
    },
    {
      "id": "result",
      "type": "output",
      "config": {"value": {"message": "${$.nodes.render}"}}
    }
  ],
  "edges": [{"source": "render", "target": "result"}]
}
```

## Use tools and models

Workflow files reference logical names and exact versions. Implementations are registered by the host application, never imported from an untrusted workflow:

```python
from agent_infra import Runtime, WorkflowSpec, compile_workflow

plan = compile_workflow(WorkflowSpec.from_dict(workflow_dict))
runtime = Runtime().register_tool("lookup", lookup, version="2026-08-09")
result = runtime.run(plan, {"ticket_id": "T-42"})
```

The CLI accepts Python callables or a JSON adapter registry:

```bash
real run workflow.plan.json \
  --tool lookup=my_app.tools:lookup@2026-08-09 \
  --provider llm=my_app.models:generate@gateway-v2 \
  --input '{"message":"hello"}'

real run workflow.plan.json \
  --adapters examples/adapters.example.json \
  --input '{"message":"hello"}'
```

Remote adapters require a host allowlist, reject redirects, cap response bodies, and keep secrets in environment variables.

## Run an online experiment

```bash
real experiment start examples/triage-experiment.json --dry-run
real experiment start examples/triage-experiment.json

real run --experiment triage_v2 \
  --unit-name organization_id \
  --unit-value acme \
  --input '{"ticket_id":"T-42","message":"cannot sign in"}'

real outcome ASSIGNMENT_ID resolved 1 --idempotency-key ticket-T-42
real experiment status triage_v2
real experiment stop triage_v2
real experiment promote triage_v2 treatment
real rollback ticket_triage
```

Assignment is not exposure: REAL records exposure only after the assigned immutable plan actually runs and its trace is stored.

## Serve workflows over HTTP

```bash
real deploy workflow.plan.json --environment prod
export REAL_API_KEY='replace-me'
real serve --host 0.0.0.0 --port 8080 --api-key-env REAL_API_KEY
```

The data plane exposes health, run, trace, experiment-status, and delayed-outcome endpoints. Public deployments should place it behind TLS, organization authentication, rate limiting, and a secret manager.

## CLI map

| Lifecycle | Commands |
|---|---|
| Define | `init`, `schema`, `validate`, `compile`, `diff`, `canvas` |
| Execute | `run`, `serve`, `deploy`, `rollback` |
| Observe | `trace`, `audit` |
| Evaluate | `eval` |
| Experiment | `experiment start/iterate/status/stop/promote`, `outcome` |

Run `real COMMAND --help` for machine-friendly argument details.

## Documentation

- [Workflow language and runtime semantics](docs/workflow-spec.md)
- [Online experimentation and attribution](docs/experimentation.md)
- [Operations, deployment, and security](docs/operations.md)
- [Architecture decision record](docs/adr/0001-core-runtime-and-ir.md)
- [Scope-to-implementation audit](docs/completion-audit.md)
- [Open-source release playbook](docs/open-source-release.md)

## Development

```bash
python -m pip install -e '.[dev,yaml]'
ruff check src tests
ruff format --check src tests
pytest
python -m build
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.

## Project boundaries

REAL intentionally does not bundle a long-term memory platform, vector database, node-level containers, or an automatic-promotion statistical engine. Those capabilities can be integrated as tools or storage backends without expanding the core workflow model. The bundled SQLite store targets a single control-plane process; high-write multi-instance deployments should provide a transactional database backend.

## License

Apache License 2.0. See [LICENSE](LICENSE).
