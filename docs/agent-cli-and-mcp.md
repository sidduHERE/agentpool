# CLI And MCP For Agents

AgentPool has two surfaces over the same control plane:

- CLI: best for coding agents with shell access.
- MCP: best for MCP-native hosts or no-shell environments.

Prefer the CLI in Codex, Claude Code, Cursor, and other coding agents that can
run shell commands. The CLI keeps transcripts, diffs, and artifacts on disk and
returns compact JSON by default. That is cheaper and more reliable than pushing
another agent's full transcript through an MCP tool result.

## CLI Agent Loop

```bash
agentpool usage-summary --refresh --json
agentpool models --provider <provider-id> --json
agentpool spawn --provider <provider-id> --model <model-id> --repo . --task "<narrow task>" --isolation read_only --json
agentpool observe <session-id> --wait-for completed,error,question,approval_prompt --timeout 120 --json
agentpool send <session-id> "<steering>"
agentpool artifacts <session-id> --json
agentpool transcript <session-id> --offset 0 --limit 4000 --json
agentpool transcript <session-id> --tail-lines 80 --json
agentpool sessions --limit 25 --offset 0 --json
agentpool collect <session-id> --json
agentpool terminate <session-id> --json
```

Use stdin for long prompts or steering:

```bash
cat task.md | agentpool spawn --provider <provider-id> --repo . --task-stdin --json
cat reply.md | agentpool send <session-id> --stdin
```

`observe` and `collect` default to summary detail. Use `--detail excerpt` or
`--detail full` only when inline worker text is worth the context cost. Use
`transcript --offset/--limit` as a cursor for large transcripts, or
`transcript --tail-lines` for a bounded tail. Use `observe --output path.json`
when an observe result should be written to disk. `sessions` is paginated by
default; use `--limit`, `--offset`, `--recent`, or `--all` deliberately.

## MCP Agent Loop

The default MCP surface is narrow:

```bash
agentpool mcp
```

It exposes inventory, cached/live usage snapshots, provider models, spawn,
observe, send, interrupt, artifact manifest, transcript paging, collect, and
terminate.

Opt into more:

```bash
agentpool mcp --toolsets default,stats,sessions,leases,worktrees
agentpool mcp --tools get_usage_summary
AGENTPOOL_MCP_LOCKDOWN=1 agentpool mcp
```

The equivalent environment variables are:

- `AGENTPOOL_MCP_TOOLSETS`
- `AGENTPOOL_MCP_TOOLS`
- `AGENTPOOL_MCP_LOCKDOWN`

MCP worker output is treated as untrusted. Summary detail omits inline worker
text. Excerpt/full detail wraps text in random nonce delimiters. Lockdown mode
suppresses inline worker output and gates transcript/event resources.

## Public Contract

AgentPool does not pick providers, rank models, store credentials, scrape
browser dashboards, merge code, or push code. The primary agent or human chooses
provider, model, isolation, and next action explicitly.
