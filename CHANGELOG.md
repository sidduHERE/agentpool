# Changelog

## Unreleased

## 0.1.9 - 2026-06-02

- Harden agent-facing CLI recovery with examples on root/group help, structured
  `models` action errors, and next-command hints for invalid output choices.
- Add side-effect-free previews for `preferences init`, `interrupt`, and
  `leases release`; add `session show --plain`.
- Format `terminate`, `collect`, and `artifacts` human output without raw Python
  dicts while preserving JSON payloads.
- Ship `python -m agentpool` support and expand CLI regression coverage.

## 0.1.8 - 2026-06-01

- Treat stale usage snapshots as age metadata, not as an unusable-provider
  reason. `usage-summary` still reports `stale` and `age_seconds`, but a stale
  cache entry no longer overrides usable quota/status data.
- Add optional `policy.usage_auto_refresh_after_seconds` for users who want
  cached usage summaries to refresh automatically after a configured age.

## 0.1.7 - 2026-06-01

- Centralize non-interactive subprocess execution behind terminal-safe helpers.
  Git, tmux client operations, provider detection, Cursor status checks, Codex
  app-server probes, and external usage helpers now detach from the host TTY by
  default.
- Add a subprocess-safety regression test so new product code cannot introduce
  raw `subprocess.run`/`Popen` calls outside the shared utility.

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
