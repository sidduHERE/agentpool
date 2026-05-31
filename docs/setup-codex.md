# Set Up AgentPool For Codex

Use this when you want Codex to see AgentPool as an MCP server and you want
AgentPool to report Codex usage/capacity.

AgentPool will inspect local state and print config. It will not log in to
Codex, store credentials, scrape browser dashboards, or edit your Codex config
file.

## 1. Install And Initialize

```bash
uv tool install agentpool-cli
agentpool init
agentpool config validate
```

For an editable checkout:

```bash
uv venv
uv pip install -e ".[dev]"
agentpool init
```

## 2. Run The Guided Codex Check

```bash
agentpool setup codex
agentpool setup all
```

This checks:

- whether `codex` is installed;
- what Codex model defaults AgentPool sees;
- whether the native Codex usage probe can read current limits;
- whether optional CodexBar enrichment is available through configured safe
  sources;
- the Codex MCP config you can paste.

For machine-readable output:

```bash
agentpool setup codex --json
```

If you only want config and local checks without a live usage probe:

```bash
agentpool setup codex --skip-usage
```

## 3. Add AgentPool To Codex

Preferred: run the one-liner Codex writes to `~/.codex/config.toml` for you:

```bash
agentpool mcp-config --client codex --absolute-command --install
```

Example command:

```bash
codex mcp add agentpool -- /absolute/path/to/agentpool mcp
```

Manual TOML paste is also supported. `agentpool setup codex` prints a block
like this:

```toml
[mcp_servers.agentpool]
command = "/absolute/path/to/agentpool"
args = ["mcp"]
startup_timeout_sec = 10
tool_timeout_sec = 300
```

Paste it into `~/.codex/config.toml` or project `.codex/config.toml` if you
prefer not to use `codex mcp add`.

## 4. Verify In Codex

From the shell:

```bash
codex mcp list
codex mcp get agentpool
```

Open Codex and run:

```text
/mcp
```

Then ask Codex:

```text
Read agentpool://skill.md. Then call
get_usage_summary(provider_id="codex-cli", refresh=false),
get_provider_models(provider_id="codex-cli"), and get_inventory before spawning
any workers. After spawn_worker, use
observe_worker for the worker control loop. If Codex shows an update prompt,
send menu choice 2 to skip. If Codex shows a directory trust prompt, send an
empty submitted message only when trusting that directory is acceptable. If it
shows hook review, send menu choice 3 unless the user approved trusting hooks.
Choose providers explicitly.
```

## 5. Use Codex Through AgentPool

From the shell:

```bash
agentpool usage-summary --provider codex-cli --refresh --json
agentpool models --provider codex-cli
agentpool spawn \
  --provider codex-cli \
  --repo . \
  --task "Inspect this repo read-only and summarize the main entry points." \
  --isolation read_only \
  --reasoning-effort high
```

Codex uses AgentPool's `provider_default` initial prompt mode, which currently
maps to the Codex CLI prompt argument path. This avoids the fragile first-turn
paste flow. Add `--initial-prompt-mode send_after_launch` only when you
explicitly want to test the older tmux paste path.

Then:

```bash
agentpool observe <session-id> --json
agentpool collect <session-id> --json
agentpool terminate <session-id>
```
