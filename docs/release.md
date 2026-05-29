# Release Checklist

Use this before tagging a release. It combines packaging, MCP registry
metadata, and onboarding verification.

## Pre-tag checks

```bash
.venv/bin/python -m pytest -q
agentpool models validate --path src/agentpool/provider_model_catalog.json --json
agentpool config validate --json
agentpool smoke --provider fake-question --repo . --json
python -m json.tool server.json >/dev/null
```

Optional secret scan (see [SECURITY.md](../SECURITY.md)):

```bash
rg -n --hidden --glob '!.git/**' --glob '!.venv/**' --glob '!dist/**' \
  '(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,})' .
```

## Version alignment

Update together before tagging:

- `pyproject.toml` `[project].version`
- `src/agentpool/__init__.py` `__version__`
- `server.json` top-level `version`
- `server.json` `packages[].version` after public PyPI publishing is enabled
- [CHANGELOG.md](../CHANGELOG.md)

The public PyPI distribution name is `agentpool-cli`. The installed console
command remains `agentpool`. Do not publish a package named `agentpool`; that
PyPI name belongs to another project.

## Tag and publish

```bash
git tag <tag>
git push origin <tag>
```

The [release workflow](../.github/workflows/release.yml) will:

1. Run tests and validation
2. Build wheel/sdist
3. Attach `dist/*` and `server.json` to the GitHub release (marked prerelease
   only for `a`/`b`/`rc` tags such as `v0.1.0a9`; `v0.1.0` is a full release)
4. Publish `agentpool-cli` to PyPI via Trusted Publishing **only** when the
   repository variable `PUBLISH_TO_PYPI` is set to `true` and the `pypi`
   deployment environment is configured. Otherwise the publish job is skipped.

See [Manual one-time setup](#manual-one-time-pypi-setup) below for the steps
that must be done on PyPI before the publish job can succeed.

## Manual one-time PyPI setup

These steps are done once, outside CI, before the first automated publish:

1. Reserve / claim the `agentpool-cli` project name on PyPI.
2. Configure a PyPI **Trusted Publisher (pending publisher)** pointing at this
   GitHub repository, the `release.yml` workflow, and the `pypi` environment.
3. Create a GitHub **`pypi` deployment environment** on the repo.
4. Set the repository variable `PUBLISH_TO_PYPI=true` to arm the publish job.

## Post-release smoke

```bash
scripts/install.sh <tag>
agentpool init
agentpool setup cursor
agentpool mcp-config --client cursor --absolute-command --install
agentpool doctor --deep
```

Raw wheel fallback:

```bash
gh release download <tag> \
  --repo sidduHERE/agentpool \
  --pattern 'agentpool_cli-*-py3-none-any.whl' \
  --dir /tmp/agentpool-release
uv tool install --force --python python3.11 \
  /tmp/agentpool-release/agentpool_cli-*-py3-none-any.whl
```

## MCP Registry

Do not publish [server.json](../server.json) to the MCP Registry until
`agentpool-cli` is available from PyPI. The committed metadata intentionally
omits `packages` so it does not advertise an unavailable install method. After
the first successful PyPI publish, add a `pypi` package entry that maps registry
consumers to:

```json
{ "command": "agentpool", "args": ["mcp"] }
```

Bump `server.json` `packages[].version` together with the project version from
then on.

Host-specific install UX remains in [docs/mcp-clients.md](mcp-clients.md).

Publish sequence:

1. Publish `agentpool-cli` to PyPI with Trusted Publishing (the release job).
2. Add the `packages` entry to `server.json`.
3. Keep the README metadata comment `mcp-name: io.github.sidduhere/agentpool`.
4. Publish [server.json](../server.json) with `mcp-publisher`.
5. Keep Docker/remote MCP out of the primary path unless AgentPool stops needing
   local tmux and local provider CLIs.
