# Provider Lifecycle Matrix

This matrix is the working truth table for AgentPool provider readiness. It is
intentionally conservative: a lifecycle cell is marked verified only after a
live or fake-provider smoke has driven the provider through that AgentPool
control-plane step.

AgentPool remains an explicit-selection control plane. This document must not
be used to rank or auto-route providers.

Compatibility note: the PRD calls the Factory coding product `factory-droid`,
but AgentPool exposes it as `droid-cli` because the installed command is
`droid`. The lifecycle matrix must not add duplicate rows for the same binary.

Cursor has two separate surfaces: `cursor` is an MCP host integration, while
`cursor-cli` is the Cursor Agent CLI worker provider driven through the local
`agent` or `cursor-agent` command.

## Legend

- `yes`: verified through AgentPool.
- `partial`: AgentPool can drive part of the lifecycle, but the provider did not
  complete the generated smoke token.
- `skipped`: intentionally not tested in the current pass.
- `pending`: implemented path exists, but this provider has not been verified.
- `n/a`: not applicable to this provider or source.
- `unknown`: no safe probe has been confirmed.

## Matrix

| Provider | Usage backend | Spawn | Observe | Send | Interrupt | Attach | Collect | Terminate | Last verified | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `fake-question` | native fake | yes | yes | yes | n/a | yes | yes | yes | 2026-05-09 | Covered by `agentpool smoke --provider fake-question`. |
| `fake-approval` | native fake | yes | yes | pending | pending | pending | pending | pending | tests | Approval fixture exists; full CLI smoke is not the default. |
| `fake-completed` | native fake | yes | yes | n/a | n/a | pending | pending | pending | tests | Completion fixture validates result detection. |
| `fake-idle` | native fake | yes | yes | pending | pending | pending | pending | pending | tests | Idle/screen-change fixture. |
| `fake-limit` | native fake | yes | yes | pending | pending | pending | pending | pending | tests | Limit warning fixture. |
| `fake-patch` | native fake | yes | yes | pending | pending | pending | pending | pending | tests | Worktree/edit fixture; use only with explicit mutating tests. |
| `codex-cli` | native + CodexBar combined | yes | yes | yes | yes | yes | yes | yes | 2026-05-09 | Verified with explicit `--model gpt-5.5` on Codex CLI v0.130.0. Known startup trust prompts can be accepted only for the smoke temp repo. The harness detects Codex update prompts and skips them before sending task text. Codex tmux submit uses `C-m`. Codex usage supports 5h and weekly windows. |
| `cursor-cli` | CodexBar cursor optional, native unknown | partial | yes | pending | yes | yes | yes | yes | 2026-05-24 | Local command is `agent` v2026.05.24-dda726e. Adapter pins models with `--model`, sets `--workspace`, and uses `--mode ask` for read-only. Local smoke reached Cursor login/auth prompt and was terminated cleanly; full completion requires `agent login` to make the installed CLI usable. `agent models` returned no account models on this machine. CodexBar cursor usage returned primary/secondary/tertiary windows through the optional backend. |
| `claude-code` | native `/usage` + CodexBar cli | yes | yes | yes | n/a | yes | yes | yes | 2026-05-09 | Verified with explicit `--model sonnet`; generated `AGENTPOOL_SMOKE_DONE`, collected, terminated, and left git clean. Startup can show Warp/tmux focus and optional MCP warnings; they did not block lifecycle detection. CodexBar safe source gives 5h usage; native `/usage` is interactive. |
| `copilot-cli` | native GitHub API + CodexBar api | yes | yes | yes | n/a | yes | yes | yes | 2026-05-09 | Verified with explicit `--model gpt-5.5`; GitHub Copilot CLI generated `AGENTPOOL_SMOKE_DONE`, collected, terminated, and left git clean. Usage percentages are available through native/CodexBar API paths. |
| `devin-cli` | native plan-status API | yes | yes | yes | n/a | yes | yes | yes | 2026-05-09 | Verified with explicit `--model codex`; generated `AGENTPOOL_SMOKE_DONE`, collected, terminated, and left git clean. Native usage includes daily, weekly, and on-demand balance. |
| `droid-cli` | unknown | yes | yes | yes | n/a | yes | yes | yes | 2026-05-09 | Verified with explicit `--model glm-5.1`, which AgentPool applies through a process-local Droid `--settings` file. This avoids the user's custom default localhost-backed model. No safe subscription quota probe confirmed. |
| `opencode` | CodexBar mapped, native unknown | skipped | skipped | skipped | skipped | skipped | skipped | skipped | skipped | Skipped for now because no OpenCode Go plan is available. CodexBar is mapped as a safe optional usage backend; native usage remains unknown. |

## Smoke Commands

Fake-provider control-plane smoke:

```bash
agentpool smoke --provider fake-question --repo .
```

Guarded real-provider read-only smoke:

```bash
agentpool smoke --provider codex-cli --repo /tmp/agentpool-smoke-repo --real-read-only --json
```

Real-provider smoke defaults to the provider's configured `smoke_model`. Override
it explicitly when needed:

```bash
agentpool smoke --provider droid-cli --model glm-5.1 --repo /tmp/agentpool-smoke-repo --real-read-only --json
```

The real-provider smoke:

- forces `read_only` isolation,
- requires the explicit `--real-read-only` flag,
- answers only known startup trust prompts for the smoke repository,
- sends one steering message,
- waits for a generated `AGENTPOOL_SMOKE_DONE` token,
- interrupts if the provider has not reached a final state,
- collects artifacts,
- terminates the tmux session,
- fails the smoke if the repository becomes dirty.

## Usage Source Policy

Default live usage is `combined`: native probe first, CodexBar as safe-source
fallback or enrichment where mapped.

```bash
agentpool usage-summary --refresh --backend combined
agentpool usage-summary --refresh --backend codexbar
agentpool usage-summary --refresh --backend native
```

CodexBar web/browser-cookie sources are not enabled by default. They can trigger
macOS keychain prompts and are outside the v0.1 safe onboarding path.
