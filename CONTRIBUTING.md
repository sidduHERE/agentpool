# Contributing

AgentPool is intentionally small and conservative. Please keep changes aligned
with the product boundary: local control plane, explicit provider selection,
truthful capacity data, no routing.

## Local Setup

```bash
uv venv
uv pip install -e ".[dev]"
agentpool init
agentpool setup cursor
agentpool doctor --deep --privacy
```

If you do not have real provider CLIs configured, use the packaged fake
providers:

```bash
agentpool smoke --provider fake-question --repo . --json
```

## Before Opening A PR

Run:

```bash
.venv/bin/python -m pytest -q
agentpool models validate --path src/agentpool/provider_model_catalog.json --json
agentpool config validate --json
agentpool smoke --provider fake-question --repo . --json
```

Do not include provider credentials, SQLite databases, artifacts, tmux logs, or
screenshots containing account data.

## Branching

- `main` is the integration branch.
- Use short feature branches such as `codex/mcp-client-docs` or
  `codex/usage-probe-fix`.
- Prefer pull requests for anything after the initial import.
- Keep releases tag-driven. Do not hand-edit generated distribution artifacts.

## Design Rules

- Do not add `provider=auto`, ranking, scoring, or provider picking.
- Do not add browser scraping or credential storage.
- Add fake-provider coverage before relying on real-provider behavior.
- Keep real-provider probes explicit and confidence-tagged.
- Prefer subprocess argument arrays.
- Keep worktree creation explicit; do not assume ownership of a user's repo
  layout unless the user requested `--isolation worktree`.
- Document what a probe reads and stores.

## Provider Changes

For a provider adapter or usage probe, update:

- tests or fake-provider coverage;
- `docs/provider-adapters.md`;
- `docs/usage-detection.md`;
- `README.md` provider matrix when user-visible.

## Releases

Releases are created from tags. See [docs/release.md](docs/release.md) for the
full checklist, `server.json` alignment, and post-release MCP install smoke.

```bash
git tag v0.1.0a0
git push origin v0.1.0a0
```

The release workflow runs tests, validates config/catalogs and `server.json`,
runs the packaged fake-provider smoke, builds wheel/sdist artifacts, attaches
`server.json` to the GitHub prerelease, and creates a GitHub prerelease. PyPI
publishing is disabled unless repository variable `PUBLISH_TO_PYPI` is set to
`true` and the PyPI trusted-publishing environment is configured.
