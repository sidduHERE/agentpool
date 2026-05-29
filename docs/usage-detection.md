# Usage Detection

Usage is best effort and confidence-tagged. Unknown is valid.

Allowed confidence values include `official`, `local_cli`, `local_config`, `provider_warning`, `observed`, `user_configured`, and `unknown`. AgentPool does not fabricate exact quotas and does not scrape browser sessions in v0.1.

Live probes are only run by explicit usage requests. Inventory remains non-invasive and reports whether a provider supports an explicit usage probe. Successful explicit probes are persisted to SQLite; `agentpool usage --cached` and `get_usage_snapshot(refresh=false)` read the latest persisted snapshots without refreshing providers.

`agentpool usage-summary` returns a `providers` map keyed by provider id. The
CLI `capacity-summary` command is a human convenience alias; MCP does not expose
a capacity alias. Each row includes `usable`, `unusable_reason`, `stale`, and
`age_seconds`. `usable` is conservative: unknown, unauthenticated, unavailable,
stale, untrusted-confidence, or below-buffer windows are unusable. The default
buffer is `policy.min_remaining_percent = 10`, and it applies to every reported
quota window.

Usage windows carry a stable `kind` in addition to provider-specific names:

- `daily`
- `5h`
- `weekly`
- `monthly`
- `session`
- `model`
- `credits`
- `on_demand`
- `unknown`

Implemented v0.1 probes:

- `codex-cli`: launches `codex -s read-only -a untrusted app-server` and reads `account/rateLimits/read`.
- `cursor-cli`: native Cursor Agent CLI usage is currently treated as unknown
  because usage is exposed through the interactive `/usage` slash command, not
  a stable non-interactive quota command. If CodexBar is installed, explicit
  `--backend codexbar` can read Cursor primary/secondary/tertiary windows.
- `claude-code`: launches a temporary tmux Claude session, sends `/usage`, parses the rendered local usage panel, and terminates the session.
- `devin-cli`: reads the existing Devin CLI credential in memory and calls Devin/Windsurf's protobuf `GetPlanStatus` endpoint via the configured Codeium API server. This returns daily included quota, weekly included quota, reset timestamps, and on-demand balance. If that fails, AgentPool falls back to a temporary tmux Devin `/usage` probe, which exposes the weekly quota and extra balance only.
- `copilot-cli`: uses an ambient GitHub token (`AGENTPOOL_COPILOT_TOKEN`, `GITHUB_TOKEN`, `GH_TOKEN`, or `gh auth token`) against GitHub's Copilot usage endpoint. It does not run a login flow or store tokens.

Optional enrichment:

- `codexbar`: optional external CLI backend for mapped providers. AgentPool only
  uses configured safe sources; browser/cookie sources are not enabled by default.
  Cursor is the exception to watch carefully: CodexBar accepts `--source cli`
  for its Cursor provider but may report the returned source as `web`; AgentPool
  exposes it only on explicit `--backend codexbar`/`combined` refresh, never
  inventory.
- `ccusage`: optional Claude Code local-log telemetry. AgentPool uses it only
  when `ccusage` is installed or `AGENTPOOL_CCUSAGE_COMMAND` is set. It runs
  `blocks --json --offline --active --no-color`, records the active 5-hour block
  in raw telemetry, and never treats it as authoritative provider quota.

CodexBar and ccusage remain optional helpers, not runtime dependencies.
