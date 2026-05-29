# Usage Probe Matrix

This matrix records the current provider-specific usage quirks discovered from local CLI probes and CodexBar reference checks.

## Codex CLI

Implemented source: `codex app-server` JSON-RPC, specifically `account/rateLimits/read`.

Reference source: CodexBar also uses `account/read` for account identity and can use OpenAI web dashboard extras when explicitly enabled. AgentPool does not use the web path.

Observed status surfaces:

- Full `/status` panel can show `5h limit` and `Weekly limit` rows with percentages and reset text.
- In tmux/no-alt-screen capture, the footer may only show compact text such as `5h 69% · weekly 32%`.
- Project MCP startup can block the TUI before `/status`; a probe must interrupt or disable MCP startup.

Implementation note: app-server RPC is the only live probe in AgentPool. TUI parsing remains parser-only test coverage.

## Claude Code

Implemented source: controlled tmux interactive `/usage`.

Observed status surface:

- `/usage` shows a rendered Usage tab. On this machine it exposes `Current session 0% used` and a reset time.
- The welcome banner may show billing mode, such as `API Usage Billing`.
- CodexBar's richer Claude paths include OAuth API and `claude.ai` cookie API. AgentPool intentionally does not use those by default.

Implementation note: parse only what the rendered CLI exposes. If weekly/model rows are not visible in the capture, return the session window rather than fabricating full subscription quota.

## Cursor Agent CLI

Native source: unknown for non-interactive quota. Cursor's public CLI changelog
mentions `/usage` in the interactive CLI, and the installed `agent` command
supports `status`, `about`, `models`, `--list-models`, `--model`, `--mode`,
and `--workspace`, but does not expose a stable `usage` subcommand.

Optional source: CodexBar `usage --provider cursor --source cli --format json`.
On this machine that returned Cursor primary, secondary, and tertiary windows
with `usedPercent` and `resetsAt` fields. CodexBar reported the result source
as `web`, so AgentPool treats this as an explicit optional backend rather than
native Cursor CLI truth.

Observed local quirks:

- Installed binary is `agent`; docs also refer to `cursor-agent`.
- `agent models` and `agent --list-models` returned `No models available for
  this account` on this machine.
- `agent status --format json` reported authenticated token state, while a
  headless print smoke still returned `Authentication required`; setup should
  surface that as a provider login problem, not silently assume readiness.
- Interactive startup can show `Press any key to log in...`.
- Cursor documents `--trust` as headless/print-only, so AgentPool does not use
  it for tmux interactive workers.

Implementation note: AgentPool pins model with `--model`, sets
`--workspace <repo>`, and uses `--mode ask` for `read_only` isolation. Native
usage remains unknown unless Cursor adds a non-interactive usage command.

## OpenCode

Safe source: `opencode stats`.

Observed status surface:

- `opencode stats` returns local session/message/token/cost history.
- `opencode stats --models --days N` returns model-level local history.
- This is not subscription quota or remaining plan capacity.

Implementation note: map it as observed/local history, not `available`.

## Droid / Factory

Safe source found so far: tmux interactive `/status`.

Observed status surface:

- `/status` shows auth state, session directory, model, current-session credit usage, and raw token usage.
- It explicitly says actual usage is at `https://app.factory.ai/settings/usage`.
- `droid usage` and `droid status` are not real subcommands; they fall through to interactive prompt behavior.

Implementation note: never probe ambiguous positional `droid usage/status/help`. Only use interactive slash commands in a controlled tmux session.

## Devin CLI

Safe sources:

- `devin auth status` for auth, plan, tier, and account metadata.
- Devin CLI's existing `~/.local/share/devin/credentials.toml` session token, used in memory against `SeatManagementService/GetPlanStatus`.
- tmux interactive `/usage` as a fallback.

Observed status surface:

- Startup banner can show `Trial · 75% remaining`.
- `/usage` can return `Quota used: 25% (remaining: 75%)`, extra usage balance, and reset time.
- The billing page and plan-status API expose separate daily and weekly included-usage quotas. On this machine, the API returned daily `93%` remaining, weekly `75%` remaining, and on-demand balance `$90.498189`.
- It may ask whether the directory is trusted before full interactive use.
- The tmux CLI output exposes one quota window, which matches the weekly included quota. It does not expose the daily bucket.

Implementation note: AgentPool uses the plan-status API first because it provides the same daily/weekly data visible in the Devin billing UI without browser scraping or new credential storage. The fallback `/usage` probe waits for the full `Quota used:` payload when possible and labels it as weekly.

## GitHub Copilot CLI

Implemented source: GitHub Copilot internal API through an ambient GitHub token.

Observed status surface:

- `gh copilot -- --help` shows the real Copilot CLI help.
- The interactive startup screen shows current-session `Requests 0 Premium`, not quota remaining.
- `/help` does not list `/status` or `/usage`.
- `gh copilot usage` and `gh copilot status` are invalid command formats.
- CodexBar uses GitHub OAuth device flow, then calls `GET https://api.github.com/copilot_internal/user` with Copilot-style editor headers.

Implementation note: AgentPool does not run a device-flow login or store the token. It uses environment tokens or `gh auth token`.

## Policy

Usage probes must be separated from inventory:

- Inventory can do binary/version detection.
- Usage probes may run slower commands and should have strict timeouts.
- Browser-cookie/keychain sources are opt-in only.
- Ambiguous positional commands are forbidden as automatic probes.
