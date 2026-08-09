# Changelog

All notable changes to REAL are documented here. The project follows semantic versioning after the first stable release.

## [Unreleased]

### Added

- Open-source project documentation, contribution guidance, security policy, and GitHub community templates.
- `real` as the primary CLI while retaining the `agent` compatibility alias.

## [0.1.0] - 2026-08-09

### Added

- JSON and optional YAML workflow language with JSON Schema validation.
- Immutable execution plans with workflow, node, and prompt hashes.
- Branches, bounded loops, parallel execution, joins, subworkflows, retries, and timeouts.
- Explicit tool/model registries plus HTTP, MCP, and OpenAI-compatible adapters.
- Run/node tracing, SQLite persistence, content redaction, and optional OpenTelemetry export.
- Offline dataset evaluation and structural workflow diffing.
- Online experiment assignment, exposure, delayed outcomes, SRM health, guardrails, iterations, promotion, audit, and rollback.
- Structured CLI, local Canvas, HTTP data plane, examples, CI, and coding-agent skill.
