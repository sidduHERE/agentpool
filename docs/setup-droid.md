# Set Up AgentPool For Droid CLI

Use this when you want AgentPool to inspect Factory Droid CLI readiness before
spawning workers.

AgentPool will not log in to Droid, store credentials, scrape browser pages, or
edit Droid config files.

Droid is a **worker** AgentPool spawns. Wire AgentPool into Cursor or another
MCP host first — see [docs/setup-cursor.md](setup-cursor.md) or
[docs/mcp-clients.md](mcp-clients.md).

## Run The Guided Check

```bash
agentpool setup droid-cli
```

Machine-readable output:

```bash
agentpool setup droid-cli --json
```

Skip live usage probing:

```bash
agentpool setup droid-cli --skip-usage
```

The setup checks:

- whether `droid` is installed;
- model defaults and model catalog metadata;
- whether a safe subscription quota probe is available (usually unknown).

Droid session usage is not the same as subscription quota. AgentPool marks
unknown quota instead of inferring it.

## Use Droid Through AgentPool

```bash
agentpool usage-summary --provider droid-cli --refresh --json
agentpool models --provider droid-cli
agentpool spawn \
  --provider droid-cli \
  --model glm-5.1 \
  --reasoning-effort high \
  --repo . \
  --task "Inspect this repo read-only and summarize the main entry points." \
  --isolation read_only
```

AgentPool pins Droid models through a process-local `--settings` file under
`~/.agentpool/runtime-settings/` so it does not mutate your global Factory
settings. Droid reasoning effort is forwarded with `--reasoning-effort` when
you provide it.
