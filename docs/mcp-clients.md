# MCP Client Setup

AgentPool runs as a local stdio MCP server:

```bash
agentpool mcp
```

Most users should not run that command by hand. Add it to your MCP host config,
then let the host start AgentPool when needed.

After setup, ask the agent to read `agentpool://skill.md`, then call
`get_usage_snapshot(refresh=false)` and `get_provider_models()` before
delegating work. Coding agents with shell access should usually prefer the
`agentpool` CLI because it keeps large worker output in artifact files.

## Verified install (recommended)

These flows were checked against each host's current docs and live CLI behavior.
AgentPool prints the exact command or deeplink for your machine; the examples
below use `agentpool` on `PATH`.

| Host | One-click deeplink? | Preferred install | Verify |
|------|---------------------|-------------------|--------|
| Cursor | Yes | Click printed `cursor://…` link | Cursor Settings → MCP |
| Claude Code | No | `claude mcp add …` | `claude mcp list`, `/mcp` |
| Codex CLI | No | `codex mcp add …` | `codex mcp list`, `/mcp` |
| Copilot CLI | No | `copilot mcp add …` | `copilot mcp list`, `/mcp show agentpool` |

Generate the host-specific helper:

```bash
agentpool mcp-config --client <client> --absolute-command --install
```

Use `--absolute-command` when the MCP host does not inherit your shell `PATH`.
GUI hosts (Cursor, Claude Desktop) are the usual case.

Official references:

- [Cursor MCP install links](https://cursor.com/docs/mcp/install-links)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [Codex `codex mcp`](https://openai-codex.mintlify.app/cli/mcp)
- [Copilot CLI MCP](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers)

### Cursor

```bash
agentpool mcp-config --client cursor --absolute-command --install
```

1. Click the printed `cursor://anysphere.cursor-deeplink/mcp/install?…` link,
   or paste it into your browser.
2. Cursor prompts to install the server.
3. Open **Settings → MCP** and confirm `agentpool` is listed with tools.

Manual fallback: `.cursor/mcp.json` or `~/.cursor/mcp.json` — see
[Cursor](#cursor) below.

### Claude Code

```bash
agentpool mcp-config --client claude-code --absolute-command --install
```

Run one printed command. Scopes mean:

- `local` — personal to the current project (Claude default)
- `project` — team-shared `.mcp.json` in the repo
- `user` — all projects via `~/.claude.json`

Example:

```bash
claude mcp add --transport stdio --scope local agentpool -- /absolute/path/to/agentpool mcp
claude mcp list
```

Inside Claude Code, run `/mcp` and confirm `agentpool` is connected.

### Codex CLI

```bash
agentpool mcp-config --client codex --absolute-command --install
```

Run the printed command:

```bash
codex mcp add agentpool -- /absolute/path/to/agentpool mcp
codex mcp list
codex mcp get agentpool
```

Open the Codex TUI and run `/mcp` to confirm AgentPool initialized.

Prefer `codex mcp add` over hand-editing TOML; Codex writes the canonical
`~/.codex/config.toml` block for you.

### GitHub Copilot CLI

Copilot CLI is usually a **worker** you spawn from AgentPool, not the app you
chat in. Wire AgentPool into Cursor or another host first if that is where you
work. Use this section when you want AgentPool tools inside Copilot CLI itself.

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

Interactive fallback: run `/mcp add` in the TUI, choose **STDIO**, and enter
the same command and args the install helper prints.

## Generate raw config

When you need JSON or TOML to paste by hand:

```bash
agentpool mcp-config --client generic
agentpool mcp-config --client claude-code --json
agentpool mcp-config --client claude-desktop --json
agentpool mcp-config --client codex
agentpool mcp-config --client cursor
agentpool mcp-config --client copilot-cli --json
```

Use `--absolute-command` with any of the above when the host cannot resolve
`agentpool` on `PATH`.

Team templates: [.cursor/mcp.json.example](../.cursor/mcp.json.example),
[.mcp.json.example](../.mcp.json.example), and [docs/examples/README.md](examples/README.md).

## Claude Code

Project scope config:

```json
{
  "mcpServers": {
    "agentpool": {
      "type": "stdio",
      "command": "agentpool",
      "args": ["mcp"],
      "env": {}
    }
  }
}
```

Save that as `.mcp.json` in the project, or use Claude Code's CLI:

```bash
claude mcp add --transport stdio --scope local agentpool -- agentpool mcp
claude mcp add --transport stdio --scope project agentpool -- agentpool mcp
claude mcp add --transport stdio --scope user agentpool -- agentpool mcp
claude mcp list
```

Expected health check:

```text
agentpool: .../agentpool mcp - ✓ Connected
```

## Codex CLI And Codex IDE Extension

Preferred: register through the Codex CLI (see [Verified install](#codex-cli)).

Manual TOML in `~/.codex/config.toml`, or project-scoped `.codex/config.toml`
in trusted projects:

```toml
[mcp_servers.agentpool]
command = "agentpool"
args = ["mcp"]
startup_timeout_sec = 10
tool_timeout_sec = 300
```

Inside the Codex TUI, run `/mcp` to confirm that AgentPool initialized.

Expected CLI check:

```text
Name       Command     Args  Status
agentpool  ...         mcp   enabled
```

## Cursor

Project scope config usually lives at `.cursor/mcp.json`; user scope usually
lives at `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "agentpool": {
      "type": "stdio",
      "command": "agentpool",
      "args": ["mcp"]
    }
  }
}
```

Open Cursor Settings > MCP to verify the server and tools.

Prefer the deeplink from `--install` when available. After editing
`.cursor/mcp.json` by hand, restart Cursor or reload MCP servers if tools do
not appear.

## Unsupported MCP hosts

AgentPool does **not** generate MCP config for:

- **VS Code / Copilot Chat in the IDE** (`.vscode/mcp.json`, `servers` key) —
  a different product from GitHub Copilot CLI. Use Cursor, Claude Code, Codex,
  or Copilot CLI as your MCP host instead.
- Legacy client aliases such as `vscode` or `copilot-vscode` — removed; they
  only duplicated the VS Code IDE format and caused confusion with
  `copilot-cli`.

## Claude Desktop

On macOS, Claude Desktop config is usually:

```text
~/Library/Application Support/Claude/claude_desktop_config.json
```

Add:

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

Restart Claude Desktop after editing the file.

## Agent Prompt

Once connected, a good first prompt is:

```text
Read agentpool://skill.md. Then call
get_usage_snapshot(refresh=false), get_provider_models(), and get_inventory() before
spawning any workers. After spawn_worker, use observe_worker for the worker
control loop; do not poll get_session/list_sessions as a substitute. Use
read_worker_transcript with offset/limit only when you need bounded transcript
pages. If Codex shows update or hook-review startup menus, answer the numbered
menu choice explicitly before observing again. Choose providers explicitly; do
not ask AgentPool to pick.
```
