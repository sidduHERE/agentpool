# Set Up Cursor Agent CLI As A Worker

Use this when you want AgentPool to spawn Cursor Agent CLI workers through the
local `agent` or `cursor-agent` command. This is separate from using Cursor as
an MCP host; for that, see `docs/setup-cursor.md`.

## Verify The CLI

```bash
agent --version
agent status --format json
agent models
```

If `agent` is missing, install Cursor CLI from Cursor's installer:

```bash
curl https://cursor.com/install -fsS | bash
```

If status is not authenticated, run:

```bash
agent login
```

## Verify AgentPool

```bash
agentpool setup cursor-cli
agentpool models --provider cursor-cli
agentpool usage --provider cursor-cli --backend codexbar --json
```

Cursor's native CLI exposes usage through the interactive `/usage` command.
AgentPool does not currently treat that as a stable native usage probe. If
CodexBar is installed, AgentPool can use CodexBar's Cursor provider as an
optional usage backend.

## Spawn A Read-Only Worker

```bash
agentpool spawn \
  --provider cursor-cli \
  --repo . \
  --model composer-2.5 \
  --task "Inspect this repo read-only and summarize the main entry points." \
  --isolation read_only
```

AgentPool launches Cursor with `--workspace <repo>` and uses `--mode ask` for
read-only isolation. Cursor's `--trust` flag is documented for headless print
mode, so AgentPool does not use it for interactive tmux workers. Cursor exposes
reasoning and 1M context as explicit model ids from `agent models`; choose those
ids with `--model` when you want them.
