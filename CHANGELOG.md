# Changelog

## Unreleased

## 0.1.6 - 2026-06-01

- Run external usage helper commands in isolated, non-interactive subprocess
  sessions so CodexBar/ccusage/GitHub CLI probes cannot inherit or disturb the
  MCP host terminal.

## 0.1.5 - 2026-05-31

- Use a certifi-backed TLS context for native usage HTTP probes so uv-tool
  installs on macOS can reach GitHub Copilot and Devin APIs reliably.

## 0.1.4 - 2026-05-31

- Add Markdown delegation preferences at `~/.agentpool/preferences.md`, surfaced
  through the CLI, MCP tools, MCP resources, inventory, usage summaries, model
  listings, and spawn responses so agents can read one user-owned preference
  source before choosing whether and how to use AgentPool.
- Bound live MCP usage refreshes so slow provider probes return partial data
  instead of hanging the host session.
- Remove deprecated `gemini-cli` from the default provider set after Google's
  transition to Antigravity CLI.

## 0.1.3 - 2026-05-29

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
