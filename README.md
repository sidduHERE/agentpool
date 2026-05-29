# AgentPool

<!-- mcp-name: io.github.sidduHERE/agentpool -->

You pay for several coding-agent subscriptions — Codex, Claude Code, Cursor,
Copilot, Gemini — but you work in one at a time. The rest sit idle until your
active provider hits its 5-hour or weekly limit, and then you stall.

AgentPool is a local Python CLI and MCP server that reads the live usage limits
of every coding-agent subscription you have and lets you — or your primary
agent — offload work to whichever one still has headroom. Use the capacity you
already pay for, and keep moving instead of hard-stopping at a cap.

It is a control plane, not an auto-router. AgentPool exposes live provider,
model, session, artifact, lease, and best-effort usage/capacity state, and runs
the work you offload as explicit worker sessions. You or your agent still choose
the provider and model — AgentPool makes the limits visible so that choice is
informed, never automatic.

The v0.1 alpha posture is conservative:

- tmux is the required runtime.
- Provider selection is explicit; `provider=auto` is rejected.
- Usage/capacity summaries are confidence-tagged and keyed by provider id.
- CodexBar and ccusage are optional usage helpers when installed or configured.
- AgentPool does not store provider credentials, scrape browser usage pages,
  rank models, merge code, or push code.

## Requirements

- Python 3.11 or newer.
- tmux on `PATH`.
- Git for worktree isolation and diff collection.
- macOS or Linux. Windows is not a v0.1 target except through WSL-like shells.

## Install

AgentPool publishes to PyPI as `agentpool-cli`; the installed command is
`agentpool`.

```bash
uv tool install agentpool-cli      # recommended
pipx install agentpool-cli         # fallback
uvx agentpool-cli --help           # zero-install try
```

Then:

```bash
agentpool setup codex
agentpool doctor --deep --privacy
```

The `agentpool-cli` package installs on macOS, Linux, and Windows, but the
runtime requires `tmux`, so the supported runtime is macOS or Linux (Windows
via WSL).

Install from source:

```bash
git clone https://github.com/sidduHERE/agentpool.git
cd agentpool
uv tool install --force .
```

Or run from a development checkout:

```bash
uv venv
uv pip install -e ".[dev]"
```

A GitHub release install (wheel pinned to a tag, no PyPI required) is also
supported:

```bash
scripts/install.sh latest
```

See [docs/install.md](docs/install.md) for first-run, upgrade, and MCP setup
notes.

## Quickstart

```bash
agentpool init
agentpool setup cursor
agentpool config validate
agentpool doctor --deep --privacy
agentpool setup all
agentpool smoke --provider fake-question --repo . --json
agentpool inventory --json
agentpool usage-summary --refresh --json
```

That last command is the one you will run most: it shows every configured
subscription's remaining limit, reset time, and a `usable` flag, so you can see
at a glance which provider to offload the next task to.

Start an explicitly selected read-only worker:

```bash
agentpool spawn \
  --provider <provider-id> \
  --repo . \
  --task "Inspect the project and ask one clarifying question." \
  --isolation read_only

agentpool observe <session-id> --wait-for completed,error,question,approval_prompt --timeout 120 --json
agentpool send <session-id> "Continue with the smallest useful check."
agentpool artifacts <session-id> --json
agentpool transcript <session-id> --tail-lines 80 --json
agentpool sessions --recent 10 --json
agentpool collect <session-id> --json
agentpool terminate <session-id> --json
```

`spawn` defaults `--initial-prompt-mode` to `provider_default`. For Codex CLI
this resolves to `arg`, which passes the first task as the Codex prompt argument
instead of relying on a paste-and-submit startup cycle. Codex workers also
accept process-local overrides such as `--reasoning-effort high` and
`--service-tier fast`; AgentPool does not edit your Codex config.

For AgentPool-created edit isolation, choose worktrees explicitly:

```bash
agentpool spawn \
  --provider <provider-id> \
  --repo . \
  --task "Make the small patch." \
  --role implementer \
  --isolation worktree

agentpool worktrees list --repo .
agentpool worktrees cleanup --session-id <session-id>
```

Worktree isolation is not forced by default. Users often have their own
worktree setup and cleanup rules, so AgentPool only creates a worktree when
requested through `--isolation worktree` or policy configuration.

## Usage And Capacity

```bash
agentpool usage-summary --refresh --json
agentpool stats --since 7d --json
agentpool usage-summary --refresh --backend codexbar --json
agentpool usage-summary --refresh --backend ccusage --provider claude-code --json
```

`usage-summary` returns a `providers` object keyed by provider id. It is not
ordered and it is not a recommendation list. Each row includes `usable`,
`unusable_reason`, quota windows, confidence, staleness, and reset timing when
the provider exposes it. The older CLI `capacity-summary` command is retained
as a human convenience alias; the MCP surface only exposes `get_usage_snapshot`
and the opt-in `get_usage_summary` tool.

The default buffer is `policy.min_remaining_percent = 10`. If any reported
quota window is below that buffer, the provider row is marked unusable for the
summary. AgentPool still does not pick an alternative provider for you.

## Provider Matrix

| Provider id | Command | Usage status in v0.1 | Model pinning |
| --- | --- | --- | --- |
| `codex-cli` | `codex` | native local app-server rate-limit probe; CodexBar optional | `--model` |
| `cursor-cli` | `agent` or `cursor-agent` | optional CodexBar Cursor usage; native CLI usage is interactive `/usage` only | `--model` + read-only `--mode ask` |
| `claude-code` | `claude` | temporary `/usage` probe; ccusage telemetry optional | `--model` |
| `devin-cli` | `devin` | Devin/Windsurf plan-status API from existing CLI auth, with `/usage` fallback | `--model` |
| `copilot-cli` | `gh copilot` | GitHub Copilot usage API from env or `gh auth token` | forwarded `--model` |
| `droid-cli` | `droid` | unknown unless surfaced by future safe probe | process-local settings file |
| `opencode` | `opencode` | configured adapter; usage unknown in this alpha | catalog-driven |

Compatibility note: the PRD calls Factory's coding product `factory-droid`, but
AgentPool exposes it as `droid-cli` because the installed command is `droid`.
Do not add a duplicate `factory-droid` inventory row unless a distinct harness
appears.

## Privacy Posture

AgentPool is local-first, but usage probes can still be sensitive because they
read existing CLI auth state and may call provider APIs on explicit refresh.

AgentPool does not:

- store provider credentials;
- read browser cookies by default;
- scrape browser dashboards;
- trigger login flows;
- silently accept paid overage.

AgentPool does store:

- SQLite session, event, usage snapshot, artifact, and lease metadata;
- transcript and artifact files under `~/.agentpool/artifacts` by default;
- generated runtime settings that are not credentials.

Run:

```bash
agentpool doctor --privacy --json
```

See [SECURITY.md](SECURITY.md) and
[docs/usage-detection.md](docs/usage-detection.md).

## MCP

Start the MCP server:

```bash
agentpool mcp
agentpool mcp --toolsets default,stats
AGENTPOOL_MCP_LOCKDOWN=1 agentpool mcp
```

Example host config:

```bash
agentpool mcp-config --client generic
```

```json
{
  "mcpServers": {
    "agentpool": {
      "command": "agentpool",
      "args": ["mcp"]
    }
  }
}
```

Verified install helpers (deeplink or one-liner shell command):

```bash
agentpool mcp-config --client cursor --absolute-command --install
agentpool mcp-config --client claude-code --absolute-command --install
agentpool mcp-config --client codex --absolute-command --install
agentpool mcp-config --client copilot-cli --absolute-command --install
```

Raw config generators:

```bash
agentpool mcp-config --client claude-code --json
agentpool mcp-config --client codex
agentpool mcp-config --client cursor
agentpool mcp-config --client claude-desktop --json
```

Use `--absolute-command` if the MCP host does not inherit your shell `PATH`.
Verified per-host steps live in [docs/mcp-clients.md](docs/mcp-clients.md).
Team templates: [.cursor/mcp.json.example](.cursor/mcp.json.example),
[.mcp.json.example](.mcp.json.example), and [docs/examples/README.md](docs/examples/README.md).
MCP Registry draft metadata: [server.json](server.json). It intentionally omits
package entries until `agentpool-cli` exists on PyPI. Release checklist:
[docs/release.md](docs/release.md).
Provider setup guides:
[Cursor](docs/setup-cursor.md),
[Cursor Agent CLI](docs/setup-cursor-cli.md),
[Codex](docs/setup-codex.md),
[Claude Code](docs/setup-claude-code.md),
[Copilot](docs/setup-copilot.md),
[Droid](docs/setup-droid.md), and
[Devin](docs/setup-devin.md).

MCP-connected agents should read these once on connect:

- `agentpool://onboarding`
- `agentpool://skill.md`

Then use tools for live operations. The default MCP toolset is deliberately
small: inventory, usage snapshot, provider models, spawn, observe, send,
interrupt, collect, artifact manifest, transcript paging, and terminate. Add
opt-in toolsets with `agentpool mcp --toolsets default,stats,sessions,leases,worktrees`.

Coding agents with shell access should prefer the CLI path. It is more
token-efficient because large worker output stays in artifact files and
`observe`/`collect` return compact manifests by default. MCP remains first-class
for MCP-native hosts and no-shell environments. See
[docs/agent-cli-and-mcp.md](docs/agent-cli-and-mcp.md).

## Development Checks

Development and CI checks are documented in [CONTRIBUTING.md](CONTRIBUTING.md).
