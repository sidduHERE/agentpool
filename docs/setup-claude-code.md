# Set Up AgentPool For Claude Code

Use this when you want AgentPool to inspect Claude Code readiness, usage, and
MCP configuration.

AgentPool will not log in to Claude, store credentials, scrape browser pages, or
edit Claude config files.

## Run The Guided Check

```bash
agentpool setup claude-code
```

Machine-readable output:

```bash
agentpool setup claude-code --json
```

Skip live usage probing:

```bash
agentpool setup claude-code --skip-usage
```

The setup checks:

- whether `claude` is installed;
- model defaults and model catalog metadata;
- native Claude usage probing where available;
- Claude Code MCP install commands for AgentPool.

## Add AgentPool To Claude Code

Preferred:

```bash
agentpool mcp-config --client claude-code --absolute-command --install
```

Run one printed command. Scope choices:

- `local` — personal to the current project (Claude default)
- `project` — team-shared `.mcp.json` in the repo
- `user` — all projects via `~/.claude.json`

Examples:

```bash
claude mcp add --transport stdio --scope local agentpool -- agentpool mcp
claude mcp add --transport stdio --scope project agentpool -- agentpool mcp
claude mcp add --transport stdio --scope user agentpool -- agentpool mcp
```

Then verify:

```bash
claude mcp list
```

Inside Claude Code, run `/mcp`. Claude should report:

```text
agentpool: .../agentpool mcp - ✓ Connected
```

See [docs/mcp-clients.md](mcp-clients.md) for manual `.mcp.json` paste and
other hosts.

## Use Claude Code Through AgentPool

```bash
agentpool usage-summary --provider claude-code --refresh --json
agentpool models --provider claude-code
agentpool spawn \
  --provider claude-code \
  --repo . \
  --task "Inspect this repo read-only and summarize the main entry points." \
  --isolation read_only
```
