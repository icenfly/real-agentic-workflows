# Contributing to REAL

Thank you for helping make reproducible agentic workflows easier to build and operate.

## Development setup

```bash
git clone https://github.com/icenfly/real-agentic-workflows.git
cd real-agentic-workflows
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,yaml,otel]'
```

Run the complete local gate before opening a pull request:

```bash
ruff check src tests
ruff format --check src tests
pytest
python -m compileall -q src tests
python -m build
```

If `src/agent_infra/schema.py` changes, regenerate the checked-in schema with `real schema --output schema/workflow-0.1.json`.

## Contribution principles

- Keep the core model small. New entities, services, protocols, and abstractions need a demonstrated requirement.
- Preserve immutable workflow versions, trace lineage, and experiment attribution.
- Prefer existing standards such as JSON Schema, MCP, and OpenTelemetry over project-specific protocols.
- Do not add credentials, customer data, generated databases, compiled plans, or evaluation secrets.
- Add tests for behavior changes and update the relevant user documentation.
- Major architecture changes should include a short ADR comparing at least three current official implementations when available.

## Pull requests

1. Open an issue first for breaking changes or substantial new capabilities.
2. Keep a pull request focused and explain its user-visible effect.
3. Include test commands and results in the pull request description.
4. Call out schema, migration, security, compatibility, or experiment-attribution impact.
5. Do not change an already published workflow meaning without a new spec or plan version.

By contributing, you agree that your contribution is licensed under the repository's Apache-2.0 License.
