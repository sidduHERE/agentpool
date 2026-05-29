# Changelog

## Unreleased

- Reposition docs and descriptions around the primary value: making full use of
  the coding-agent subscriptions you pay for by offloading work to a provider
  with headroom when the active one nears its limit. Updates README, package and
  registry descriptions, onboarding/skill/quickstart/architecture docs, the CLI
  help, and the MCP server instructions. No behavior change.

## 0.1.2 - 2026-05-29

- Fix MCP namespace casing to `io.github.sidduHERE/agentpool` in `server.json`
  and the README `mcp-name` comment so it matches the GitHub-verified namespace
  required by the MCP registry.

## 0.1.1 - 2026-05-29

- Add command descriptions to `doctor`, `init`, `inventory`, `usage`,
  `capacity-summary`, `setup`, `onboard`, `providers`, and `models` so `--help`
  documents the full surface.
- `spawn` now returns a top-level `session_id` (mirroring the nested
  `session.id`) so it matches `observe`/`send`/`collect`; `terminate` now
  includes `session_id` alongside `ok`/`state`.
- `agentpool://quickstart` now serves a distinct quickstart guide instead of
  aliasing `agentpool://skill.md`.
- Publish `agentpool-cli` to PyPI; add the PyPI package entry to `server.json`
  for the MCP registry.

## 0.1.0 - 2026-05-29

- First public release of AgentPool: local Python CLI, MCP server,
  tmux-backed worker lifecycle, explicit provider/model selection, SQLite state,
  packaged fake providers, conservative usage probes, agent-friendly CLI output,
  lean MCP toolsets, redaction, session reconciliation, and worktree utilities.
