# AgentPool Onboarding

AgentPool helps you use every coding-agent subscription you pay for. It reads
the live usage limits of each provider you have (Codex, Claude Code, Cursor,
Copilot, Devin, Droid, ...) so you can offload work to whichever still has
headroom instead of stalling when your active subscription hits its 5-hour or
weekly cap.
It is a control plane for explicitly selected coding-agent CLIs: it does not
route automatically and it does not choose a provider for you.

## Human CLI Setup

1. Install the package in the repo environment:

```bash
uv venv
uv pip install -e ".[dev]"
```

2. Initialize AgentPool and wire your MCP host:

```bash
agentpool init
agentpool setup cursor
agentpool config validate
agentpool doctor --deep --privacy
```

3. Check configured workers and usage backends:

```bash
agentpool providers
agentpool models
agentpool setup all
agentpool usage-summary --refresh
```

Usage probes support three live backends:

- `native`: AgentPool's provider-specific probes.
- `codexbar`: CodexBar CLI, if installed.
- `ccusage`: optional Claude Code local-log telemetry.
- `combined`: native first, CodexBar and ccusage as safe-source
  fallback/enrichment where mapped.

AgentPool only uses CodexBar's explicit non-browser sources by default. Browser
cookie or web dashboard sources are not enabled implicitly because they can
trigger macOS keychain prompts.

4. Prove the control plane with a fake worker before using a real provider:

```bash
agentpool smoke --provider fake-question --repo .
```

Real-provider smoke tests require an explicit read-only opt-in:

```bash
agentpool smoke --provider codex-cli --repo /tmp/agentpool-smoke-repo --real-read-only
```

Real providers use configured smoke models by default. For Droid, AgentPool pins
`glm-5.1` through a process-local settings file so a custom user default does
not accidentally route to a local proxy:

```bash
agentpool smoke --provider droid-cli --model glm-5.1 --repo /tmp/agentpool-smoke-repo --real-read-only
```

Model defaults and harness quirks are catalog driven:

```bash
agentpool models --provider droid-cli
agentpool models --provider codex-cli --json
agentpool models --provider cursor-cli --json
agentpool models validate --path ~/.agentpool/models.json
agentpool config validate
```

Layer user JSON catalogs with `model_catalog_paths` in `~/.agentpool/config.yaml`;
direct `providers.<id>.metadata.default_model` overrides still win. See
`docs/model-catalog.md`.

Compatibility note: the PRD calls the Factory coding product `factory-droid`,
but AgentPool exposes it as `droid-cli` because the installed command is
`droid`. Do not add a second inventory row unless a distinct Factory Droid
harness appears.

Cursor note: `cursor` is the MCP host target. `cursor-cli` is the worker
provider for Cursor Agent CLI through the local `agent`/`cursor-agent` command.

Use the lower-level lifecycle commands when you need to inspect or steer each
step manually:

```bash
agentpool spawn --provider fake-question --repo . --task "Ask one question." --isolation read_only
agentpool observe <session-id> --wait-for completed,error,question,approval_prompt --timeout 60 --json
agentpool send <session-id> "Continue read-only."
agentpool artifacts <session-id> --json
agentpool transcript <session-id> --tail-lines 80 --json
agentpool collect <session-id> --json
agentpool terminate <session-id> --json
```

## MCP Host Setup

Use this host config shape:

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

For verified install (deeplink or one-liner shell command):

```bash
agentpool setup cursor
agentpool mcp-config --client cursor --absolute-command --install
agentpool mcp-config --client claude-code --absolute-command --install
agentpool mcp-config --client codex --absolute-command --install
agentpool mcp-config --client copilot-cli --absolute-command --install
```

For raw config paste:

```bash
agentpool mcp-config --client claude-code --json
agentpool mcp-config --client codex
agentpool mcp-config --client cursor
```

See `docs/mcp-clients.md` for verified steps, manual fallbacks, and Claude
Desktop paths.

Agents may read these default resources at startup, or when they notice the
resource is missing from their context:

- `agentpool://onboarding`
- `agentpool://skill.md`
- `agentpool://sessions/{session_id}/transcript`
- `agentpool://sessions/{session_id}/events`
- `agentpool://artifacts/{session_id}`

Do not inject full resources into every prompt if the agent already has them.
The live state is in tools. Coding agents with shell access should usually use
the CLI because it keeps transcripts and artifacts on disk until explicitly read.

## Agent Operating Loop

1. Read usage and model state (`agentpool usage-summary --json`, `agentpool models --json`, or the matching MCP tools).
2. Pick the provider explicitly.
3. Use `read_only` for exploration and choose `worktree` explicitly only when
   AgentPool should create an isolated worktree.
4. Spawn one narrow worker.
5. Observe until question, approval, completion, error, or timeout.
6. Send steering or interrupt deliberately.
7. Use advisory file leases when multiple workers may touch the same files.
8. Read bounded transcript pages only when the manifest/summary is not enough.
9. Use paginated `sessions` / `list_sessions` reads for fleet metadata.
10. Read the artifact manifest, then collect artifacts.
11. Terminate sessions when done.

AgentPool never merges, pushes, silently accepts overage, stores provider
credentials, scrapes browser pages, or ranks providers.
