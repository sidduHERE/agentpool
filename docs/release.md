# Release Checklist

Use this before tagging a private alpha release. It combines packaging, MCP
registry metadata, and onboarding verification.

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
- `server.json` top-level `version`
- `server.json` `packages[].version` after public PyPI publishing is enabled
- [CHANGELOG.md](../CHANGELOG.md)

The public PyPI distribution name is `agentpool-cli`. The installed console
command remains `agentpool`. Do not publish a package named `agentpool`; that
PyPI name belongs to another project.

## Tag and publish a private alpha

```bash
git tag <tag>
git push origin <tag>
```

The [release workflow](../.github/workflows/release.yml) will:

1. Run tests and validation
2. Build wheel/sdist
3. Attach `dist/*` and `server.json` to the GitHub prerelease
4. Skip PyPI publishing for the private alpha line.

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

## MCP Registry (future public release)

Do not publish [server.json](../server.json) to the MCP Registry until
`agentpool-cli` is available from PyPI. The committed private-preview metadata
intentionally omits `packages` so it does not advertise an unavailable install
method. When the PyPI package exists, add a `pypi` package entry that maps
registry consumers to:

```json
{ "command": "agentpool", "args": ["mcp"] }
```

Host-specific install UX remains in [docs/mcp-clients.md](mcp-clients.md).

For a later public release:

1. Publish `agentpool-cli` to PyPI with Trusted Publishing.
2. Keep the README metadata comment `mcp-name: io.github.sidduhere/agentpool`.
3. Publish [server.json](../server.json) with `mcp-publisher`.
4. Keep Docker/remote MCP out of the primary path unless AgentPool stops needing
   local tmux and local provider CLIs.
