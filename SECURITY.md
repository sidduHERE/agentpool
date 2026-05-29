# Security Policy

AgentPool is a local control plane for already-installed coding-agent CLIs. It
does not provide a remote service and it does not store provider credentials.

## Supported Versions

The current alpha line is `0.1.x`. Security fixes target the latest alpha
unless a release notes entry says otherwise.

## Reporting A Vulnerability

Use GitHub private vulnerability reporting:
<https://github.com/sidduHERE/agentpool/security/advisories/new>.

If that route is unavailable while the repository is still private, open a
minimal private issue asking for maintainer contact. Do not include working
provider tokens, session cookies, private keys, or paid-account screenshots in
public issues or public discussions.

Useful reports include:

- the AgentPool version or commit;
- operating system and Python version;
- provider id and backend involved;
- exact command run, with credentials redacted;
- whether the issue affected SQLite state, artifacts, tmux sessions, or usage
  refresh.

## Data AgentPool Reads

AgentPool can read local provider CLI state only when a user or MCP client
explicitly asks for live usage refresh. Inventory is intentionally non-invasive.

Current explicit usage probes may read:

- Codex local app-server usage data;
- Claude Code `/usage` output through a temporary tmux session;
- Devin CLI credential files in memory, then Devin/Windsurf plan-status APIs;
- GitHub tokens from `AGENTPOOL_COPILOT_TOKEN`, `GITHUB_TOKEN`, `GH_TOKEN`, or
  `gh auth token`;
- optional CodexBar CLI safe sources;
- optional ccusage local Claude Code telemetry.

AgentPool does not read browser cookies by default, scrape browser dashboards,
or store refreshed provider credentials.

## Data AgentPool Stores

By default AgentPool stores local state under `~/.agentpool`:

- `~/.agentpool/config.yaml`;
- `~/.agentpool/agentpool.sqlite`;
- `~/.agentpool/artifacts/<session-id>/...`;
- non-secret runtime settings under `~/.agentpool/runtime-settings`.

SQLite stores sessions, events, usage snapshots, artifact manifests, and
advisory file leases. Transcript and screen excerpts are redacted before
persistence with conservative patterns, but redaction is a safety net, not a
license to paste secrets into worker prompts.

## Security Boundaries

- tmux is the v0.1 runtime boundary. It is process control, not a sandbox.
- `read_only` isolation is a prompt and launch-mode discipline where providers
  support it, not a filesystem mount policy.
- `worktree` isolation is explicit. AgentPool creates and cleans only the
  worktrees it owns.
- AgentPool never merges, pushes, or accepts paid overage silently.
- Provider/model selection remains with the calling agent or human.

## Maintainer Checklist Before Release

Run these before tagging a public release:

```bash
.venv/bin/python -m pytest -q
agentpool models validate --path src/agentpool/provider_model_catalog.json --json
agentpool config validate --json
agentpool smoke --provider fake-question --repo . --json
python -m json.tool server.json >/dev/null
gh secret list --repo sidduHERE/agentpool
gh release view <tag> --repo sidduHERE/agentpool --json isDraft,isPrerelease,assets
rg -n --hidden --glob '!.git/**' --glob '!.venv/**' --glob '!dist/**' --glob '!build/**' --glob '!**/__pycache__/**' --glob '!*.pyc' '(sk-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|-----BEGIN (RSA|OPENSSH|EC|DSA|PRIVATE) KEY-----)' .
```
