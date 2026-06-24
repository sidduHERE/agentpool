# Changelog

## Unreleased

## 0.1.13 - 2026-06-24

- Add `poll_worker` as a fast MCP progress snapshot and make
  `observe_worker(timeout_seconds=0/1)` behave as a true fast poll.
- Bound MCP observe waits below common host executor timeouts and return timeout
  metadata instead of leaving coordinators with dropped tool calls.
- Honor `include_recent_log` for MCP observation, refresh
  `summary.partial.md` while workers are running, and avoid full transcript
  scans on observe/poll artifact manifests.
- Accept terminal state names such as `COMPLETED`, `FAILED`, and `CANCELLED` in
  `wait_for` and pass observe deadlines into runtime capture calls.
- Mark `terminate_worker` as explicit, side-effecting, idempotent cleanup in
  MCP annotations without treating it as user-data destruction.

## 0.1.12 - 2026-06-06

- Add optional Terminal Control runtime support while keeping tmux as the
  default worker runtime.
- Fix Claude Code steering submission by using provider-specific submit keys
  for both initial prompts and live `send_worker_message` calls.
- Restore MCP capacity/cache compatibility aliases, add searchable MCP tool
  descriptions and annotations, and make Cursor Agent CLI detection robust when
  MCP hosts launch AgentPool with a minimal `PATH`.
- Add real-provider and fake-provider coverage for tmux and Terminal Control,
  plus focused regression coverage for MCP aliases and provider binary lookup.

## 0.1.11 - 2026-06-05

- Refresh provider model catalogs from current Claude Code, Codex, Cursor,
  Droid, and OpenCode surfaces, including Claude Opus 4.8, Claude Code 1M
  suffixes, and Cursor's explicit 1M/reasoning variants.
- Forward provider reasoning controls for Claude Code (`--effort`) and Droid
  (`--reasoning-effort`) while keeping Codex reasoning/service-tier overrides
  process-local.
- Refresh stale installed catalog metadata from the embedded catalog without
  overriding user-selected default or smoke models.

## 0.1.10 - 2026-06-03

- Add an AI-agent start section to root help pointing agents at bundled skill
  guidance before they infer workflows from flags.
- Add `agentpool skills` with `list`, `get`, and `path` so agents can load
  version-matched AgentPool usage guidance from the installed CLI.
- Include examples with packaged docs so `agentpool skills get core --full`
  returns both the skill and copy-paste CLI flows.

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
