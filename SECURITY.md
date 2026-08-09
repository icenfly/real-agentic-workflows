# Security Policy

## Supported versions

Before the first stable release, security fixes are applied to the latest release on the `main` branch.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private vulnerability reporting flow:

<https://github.com/icenfly/real-agentic-workflows/security/advisories/new>

Include the affected version or commit, impact, reproduction steps, and any suggested mitigation. Avoid placing real credentials, private customer data, or destructive payloads in the report. The maintainer will acknowledge a complete report as soon as practical, coordinate remediation and disclosure, and credit reporters who wish to be named.

## Security boundaries

- Workflow files are data and cannot import arbitrary Python implementations.
- Tools and model providers must be explicitly registered with versions.
- Remote adapters require a host allowlist, reject redirects, and limit response size.
- Non-loopback HTTP serving requires a bearer token, but production still needs TLS, organization authentication, rate limits, and secret management at the edge.
- SQLite is a single-control-process default, not a multi-tenant authorization boundary.
- MCP annotations and all remote tool/model output are untrusted input.
