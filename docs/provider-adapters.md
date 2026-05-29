# Provider Adapters

Adapters detect local CLI binaries, report conservative auth and usage state, build launch commands, and provide worker prompt text.

Default real-provider behavior is intentionally limited:

- Detect binary with `shutil.which`.
- Probe version with a short timeout.
- Report auth as `unknown` unless a safe probe exists.
- Report usage as `unknown` unless a safe explicit probe exists.
- Launch through tmux using the provider's normal CLI command.
- Pin a requested model only when the provider exposes a safe process-local
  mechanism for it.

Safe explicit usage probes currently exist for `codex-cli`, `claude-code`, `devin-cli`, and `copilot-cli`.
`cursor-cli` has an optional CodexBar usage path; the native Cursor Agent CLI
currently exposes usage through interactive `/usage`, not a stable
non-interactive quota command. These probes run only for `agentpool usage`;
inventory does not launch provider TUIs or network quota probes.

Compatibility note: the PRD calls the Factory coding product `factory-droid`,
but AgentPool exposes it as `droid-cli` because the installed command is
`droid`. This avoids duplicate inventory rows for the same binary and runtime
adapter.

Model pinning behavior:

- `claude-code`, `codex-cli`, `cursor-cli`, `copilot-cli`, and `devin-cli` use
  their native `--model` option.
- `codex-cli` also supports per-session `reasoning_effort` and `service_tier`
  overrides through Codex `-c` config overrides. AgentPool does not edit
  `~/.codex/config.toml`.
- `codex-cli` defaults initial prompt delivery to `arg` so the worker receives
  the first task from the Codex CLI prompt argument instead of requiring an
  immediate paste/submit cycle.
- `copilot-cli` is invoked as `gh copilot -- ...` when forwarding Copilot flags.
- `cursor-cli` uses `agent` or `cursor-agent`, adds `--workspace <path>`, and
  uses `--mode ask` for `read_only` isolation because Cursor documents Ask mode
  as read-only Q&A. Its provider default initial prompt mode is `arg`, matching
  Cursor's documented `[prompt...]` argument.
- `droid-cli` does not expose interactive `--model`; AgentPool
  writes a minimal `~/.agentpool/runtime-settings/droid-<model>.json` and starts
  Droid with `--settings <path>`.
- AgentPool does not edit or persist provider credentials or user defaults.

Tmux submit behavior:

- Most providers submit pasted text with terminal Enter.
- `codex-cli` submits with `C-m` in tmux.
- Short menu selections such as startup trust or update prompts still use normal
  Enter so they do not go through provider composer shortcuts.

Fake providers under `src/agentpool/fixtures/fake_agents/` are the executable contract for v0.1.
They are packaged with AgentPool so `agentpool smoke --provider fake-question`
works after wheel or pipx installs without relying on the test tree.

Cursor is also supported as an MCP host target through `agentpool setup cursor`.
That host setup is separate from the `cursor-cli` worker provider.
