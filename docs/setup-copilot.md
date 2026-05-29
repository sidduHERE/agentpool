# Set Up AgentPool For GitHub Copilot CLI

Use this when you want AgentPool to inspect GitHub Copilot CLI readiness and
usage.

AgentPool will not log in to GitHub, store credentials, scrape browser pages, or
edit GitHub CLI config files.

Copilot CLI is usually a **worker** you spawn through AgentPool, not the MCP
host you chat in. If you work in Cursor, Claude Code, or Codex, wire AgentPool
there first — see [docs/mcp-clients.md](mcp-clients.md).

AgentPool does **not** support VS Code Copilot Chat (`.vscode/mcp.json`) as an
MCP host. That is a separate IDE product from GitHub Copilot CLI. Use
`copilot-cli` below only when Copilot CLI itself is your chat host.

## Run The Guided Check

```bash
agentpool setup copilot-cli
```

Machine-readable output:

```bash
agentpool setup copilot-cli --json
```

Skip live usage probing:

```bash
agentpool setup copilot-cli --skip-usage
```

The setup checks:

- whether `gh` is installed;
- whether the Copilot adapter can use the configured command;
- model defaults and model catalog metadata;
- native Copilot usage through ambient GitHub auth where available.

## Add AgentPool To Copilot CLI (optional)

When Copilot CLI is your chat host:

```bash
agentpool mcp-config --client copilot-cli --absolute-command --install
```

Run the printed command:

```bash
copilot mcp add agentpool -- /absolute/path/to/agentpool mcp
copilot mcp list
copilot mcp get agentpool
```

Inside Copilot CLI, `/mcp show agentpool` should list AgentPool tools.

## Use Copilot Through AgentPool

```bash
agentpool usage-summary --provider copilot-cli --refresh --json
agentpool models --provider copilot-cli
agentpool spawn \
  --provider copilot-cli \
  --repo . \
  --task "Inspect this repo read-only and summarize the main entry points." \
  --isolation read_only
```

If usage probing reports auth failure, run `gh auth login` or refresh your
GitHub auth outside AgentPool.
