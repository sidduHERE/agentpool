# Release Checklist

Use this before tagging a release. It combines packaging, MCP registry
metadata, and onboarding verification.

## Pre-tag checks

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/check_release_metadata.py
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
- `server.json` `packages[].version`
- [CHANGELOG.md](../CHANGELOG.md)

The public PyPI distribution name is `agentpool-cli`. The installed console
command remains `agentpool`, with an `agentpool-cli` console-script alias for
`uvx agentpool-cli mcp`. Do not publish a package named `agentpool`; that PyPI
name belongs to another project.

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

Keep the external setup in
[PyPI and GitHub trusted-publisher state](#pypi-and-github-trusted-publisher-state)
in place before tagging.

## PyPI and GitHub trusted-publisher state

The `agentpool-cli` project is already live on PyPI, and the `v0.1.12`
distributions were uploaded using Trusted Publishing from
`sidduHERE/agentpool` via `.github/workflows/release.yml` and the `pypi`
environment.

Keep these exact external settings:

- PyPI project: `agentpool-cli`
- PyPI trusted publisher owner: `sidduHERE`
- PyPI trusted publisher repository: `agentpool`
- PyPI trusted publisher workflow: `release.yml`
- PyPI trusted publisher environment: `pypi`
- GitHub deployment environment: `pypi`
- GitHub repository variable: `PUBLISH_TO_PYPI=true`

The publish job has `id-token: write` only on the PyPI job and no PyPI token
secret is required.

Recommended GitHub hardening:

- Add required reviewers to the `pypi` deployment environment so a tag push does
  not immediately publish without a human release approval.
- If deployment branch/tag rules are enabled, allow release tags matching `v*`;
  do not restrict the environment to branch deployments only.
- Keep PyPI API tokens out of GitHub secrets for this project.

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

[server.json](../server.json) now advertises the published `agentpool-cli` PyPI
package. Keep its top-level `version` and `packages[].version` aligned with
`pyproject.toml` and `src/agentpool/__init__.py`.

Host-specific install UX remains in [docs/mcp-clients.md](mcp-clients.md).

Publish sequence:

1. Publish `agentpool-cli` to PyPI with Trusted Publishing (the release job).
2. Keep [server.json](../server.json) pointed at the same published version.
3. Keep the README metadata comment `mcp-name: io.github.sidduHERE/agentpool`.
   The namespace casing must match the GitHub username exactly (`sidduHERE`, not
   `sidduhere`); the registry grants `io.github.<login>/*` with the login's
   casing. The registry also validates that the README **published in the PyPI
   package** contains this `mcp-name`, so any casing fix must ship in a PyPI
   release *before* `mcp-publisher publish` points `packages[].version` at it.
4. Publish [server.json](../server.json) with `mcp-publisher`.
5. Keep Docker/remote MCP out of the primary path unless AgentPool stops needing
   local tmux and local provider CLIs.
