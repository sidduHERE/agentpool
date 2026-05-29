# MCP Example Templates

Team onboarding templates for common MCP hosts. AgentPool does not auto-edit
host config files; copy these into your project and substitute the absolute
`agentpool` path.

Resolve the path:

```bash
agentpool mcp-config --client cursor --absolute-command --install
```

Or print raw JSON:

```bash
agentpool mcp-config --client cursor --absolute-command --json
```

## Files

| Template | Copy to | Host |
|----------|---------|------|
| [../.cursor/mcp.json.example](../.cursor/mcp.json.example) | `.cursor/mcp.json` or `~/.cursor/mcp.json` | Cursor |
| [../.mcp.json.example](../.mcp.json.example) | `.mcp.json` | Claude Code (project scope) |

Prefer verified install helpers when available:

```bash
agentpool setup cursor
agentpool mcp-config --client cursor --absolute-command --install
agentpool mcp-config --client claude-code --absolute-command --install
agentpool mcp-config --client codex --absolute-command --install
```

See [docs/mcp-clients.md](../mcp-clients.md).
