# Open-source release playbook

The public identity is:

- Project: **REAL Framework** — Reproducible Experimentation for Agentic Logic
- Repository: `icenfly/real-agentic-workflows`
- Python distribution: `real-agentic-workflows`
- Primary CLI: `real`
- Compatibility CLI and import namespace: `agent`, `agent_infra`
- License: Apache-2.0

Both the proposed GitHub repository path and PyPI distribution name returned 404 when checked on 2026-08-09. Recheck immediately before creation or publication because names are not reserved until created.

## Local release gate

```bash
python -m pip install -e '.[dev,yaml]'
ruff check src tests
ruff format --check src tests
pytest
python -m compileall -q src tests
python -m build
```

Also install the newly built Wheel into a clean virtual environment and run `real --version`, `real init`, `real validate`, `real compile`, `real eval`, and `real run`.

## Create the GitHub repository

Authenticate the intended account and verify it before making anything public:

```bash
gh auth login -h github.com -w
gh auth status
gh api user --jq .login
```

The final command must print `icenfly`.

Initialize this directory as an independent repository. Do not commit it through the unrelated parent repository:

```bash
git init -b main
git add .
git commit -m "Initial open-source release of REAL Framework"
gh repo create icenfly/real-agentic-workflows \
  --public \
  --source=. \
  --remote=origin \
  --push \
  --description "Reproducible agentic workflows with observability and online experimentation"
```

Then enable community features and metadata:

```bash
gh repo edit icenfly/real-agentic-workflows \
  --enable-discussions \
  --enable-issues \
  --add-topic ai-agents \
  --add-topic agentic-workflows \
  --add-topic experimentation \
  --add-topic mcp \
  --add-topic observability \
  --add-topic python
```

In GitHub Settings, enable private vulnerability reporting, require pull requests and passing CI on `main`, disallow force pushes, and configure PyPI Trusted Publishing before adding a release workflow.

## First release

1. Confirm CI passes on GitHub.
2. Build and inspect the sdist and Wheel.
3. Create an annotated `v0.1.0` tag and GitHub Release from `CHANGELOG.md`.
4. Configure PyPI Trusted Publishing for this exact owner/repository and a protected `pypi` environment.
5. Publish only from a tag-based GitHub Actions workflow after Trusted Publishing is configured.

Never place a long-lived PyPI token or GitHub personal access token in the repository.
