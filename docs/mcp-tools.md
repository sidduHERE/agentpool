# MCP Tools

AgentPool's MCP server is intentionally lean. Coding agents with shell access
should prefer the `agentpool` CLI; MCP is for MCP-native hosts and environments
where shell commands are not available.

Start the default server:

```bash
agentpool mcp
```

Opt into extra toolsets only when a host needs them:

```bash
agentpool mcp --toolsets default,stats
agentpool mcp --toolsets default,sessions,leases,worktrees
AGENTPOOL_MCP_LOCKDOWN=1 agentpool mcp --toolsets default
```

Environment mirrors:

- `AGENTPOOL_MCP_TOOLSETS=default,stats`
- `AGENTPOOL_MCP_TOOLS=get_stats`
- `AGENTPOOL_MCP_LOCKDOWN=1`

Unknown toolsets or tool names fail at server startup.

## Default Toolset

The default toolset is the smallest worker lifecycle surface:

- `get_inventory`
- `get_usage_summary`
- `get_capacity_summary`
- `get_usage_snapshot`
- `get_cached_usage_snapshot`
- `get_provider_models`
- `get_delegation_preferences`
- `spawn_worker`
- `observe_worker`
- `send_worker_message`
- `interrupt_worker`
- `collect_worker_artifacts`
- `get_artifact_manifest`
- `read_worker_transcript`
- `terminate_worker`

Compatibility aliases:

- `get_capacity_summary`: alias for `get_usage_summary`
- `get_cached_usage_snapshot`: alias for `get_usage_snapshot(refresh=false)`

Use `get_usage_summary(refresh=false)` for the compact provider-capacity view.
Use `get_usage_snapshot(refresh=false)` only when you need raw snapshots; cached
mode is the default for both. MCP refreshes intentionally avoid interactive
provider TUI probes that could interfere with the host agent session. Live MCP
refreshes are also time bounded; a slow provider returns an unknown row and the
response sets `partial=true` instead of letting the MCP connection sit open.
Use the CLI for a complete live refresh from shell-capable coding agents. Use
the `usage` toolset if you need `validate_model_catalog` or
`filter_candidates`.

## Opt-In Toolsets

- `usage`: `validate_model_catalog`, `filter_candidates`
- `stats`: `get_stats`, `get_stats_card`
- `sessions`: `list_sessions`, `get_session`, `attach_info`, `send_worker_keys`
- `leases`: `acquire_file_lease`, `list_file_leases`, `release_file_lease`
- `worktrees`: `list_worktrees`, `cleanup_worktree`

Each MCP server process scopes active-session policy to its coordinator. If one
Codex parent and one Claude parent both use AgentPool, each sees its own active
session count by default. Operator-wide session inspection is available through
the `sessions` toolset with `include_all=true`. `list_sessions` is paginated by
default; pass `limit` and `offset` for cursor-style reads.

## Worker Loop

Use `observe_worker` as the control loop. `get_session` and `list_sessions` are
metadata lookups, not substitutes for observation.

Recommended loop:

1. `get_usage_summary(provider_id=..., refresh=false)`
2. `get_provider_models(provider_id=...)`
3. `spawn_worker(provider_id=..., model=..., repo_path=..., task=..., isolation="read_only")`
4. `observe_worker(session_id=..., wait_for=["completed","error","question","approval_prompt"], timeout_seconds=120)`
5. `send_worker_message(...)` or `interrupt_worker(...)` when needed
6. `get_artifact_manifest(...)`
7. `read_worker_transcript(session_id=..., offset=..., limit=...)` only when a bounded transcript page is needed
8. `collect_worker_artifacts(...)`
9. `terminate_worker(...)` when the session is no longer useful

`spawn_worker.task` must be the concrete delegated instruction. Placeholder text
such as `Improve documentation in @filename` is rejected.

`spawn_worker.initial_prompt_mode` defaults to `provider_default`. For
`codex-cli`, AgentPool resolves this to `arg` because the Codex CLI accepts the
initial prompt as a process argument and avoids first-turn paste/submission
drift.

`spawn_worker.reasoning_effort` and `spawn_worker.service_tier` are optional
provider controls. For `claude-code`, reasoning becomes `--effort`; for
`droid-cli`, it becomes `--reasoning-effort`; for `codex-cli`, reasoning and
service tier become process-local Codex overrides. AgentPool does not edit the
user's provider config.

## Output Detail And Lockdown

Worker text is untrusted model output. `observe_worker` and
`collect_worker_artifacts` default to `detail="summary"`, which returns state,
readiness metadata, and artifact paths without inline worker text.

Use `detail="excerpt"` or `detail="full"` only when the coordinator needs text
inline. Inline worker text is wrapped in a random per-call delimiter:

```text
BEGIN_UNTRUSTED_WORKER_OUTPUT_<nonce>
...
END_UNTRUSTED_WORKER_OUTPUT_<nonce>
```

Transcript/event resources also treat their contents as untrusted: outside
lockdown they return bounded, nonce-delimited JSON payloads rather than raw text.
`--lockdown` or `AGENTPOOL_MCP_LOCKDOWN=1` suppresses inline worker text and
gates transcript/event resources. Artifact manifests remain available, but raw
worker-text files are marked `gated`. `read_worker_transcript` returns a
lockdown block instead of transcript text when lockdown is enabled.

## Resources And Prompts

The default resource set is non-duplicative:

- `agentpool://onboarding`
- `agentpool://skill.md`
- `agentpool://preferences.md`
- `agentpool://sessions/{session_id}/transcript`
- `agentpool://sessions/{session_id}/events`
- `agentpool://artifacts/{session_id}`

The default prompts are:

- `agentpool_quickstart`
- `agentpool_delegate_read_only`

Resources and prompts are registered through the same server selection path as
tools. A stats-only MCP server does not expose the default onboarding resources
or prompts.
