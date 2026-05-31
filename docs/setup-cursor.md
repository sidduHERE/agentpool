# Set Up AgentPool For Cursor

Use this when Cursor is your MCP host and you want AgentPool tools in chat.

AgentPool will inspect whether the local `agentpool` command resolves and print
Cursor install helpers. It will not edit `.cursor/mcp.json` for you.

## Run The Guided Check

```bash
agentpool setup cursor
```

Machine-readable output:

```bash
agentpool setup cursor --json
```

The setup checks:

- whether `agentpool` resolves on `PATH` (or via `--absolute-command`);
- Cursor MCP install output (deeplink + `.cursor/mcp.json` fallback).

## Install AgentPool In Cursor

Preferred:

```bash
agentpool mcp-config --client cursor --absolute-command --install
```

1. Click the printed `cursor://…` deeplink, or paste it into your browser.
2. Confirm the install prompt in Cursor.
3. Open **Settings → MCP** and verify `agentpool` lists tools.

Manual fallback: copy `.cursor/mcp.json.example` to `.cursor/mcp.json` in your
project (or use `~/.cursor/mcp.json` for user scope) and replace `command` with
the absolute path from:

```bash
agentpool mcp-config --client cursor --absolute-command
```

See [docs/mcp-clients.md](mcp-clients.md) for other hosts.

## After Connect

Ask Cursor to read `agentpool://skill.md`, then call
`get_usage_summary(refresh=false)` and `get_provider_models()` before spawning
workers.
