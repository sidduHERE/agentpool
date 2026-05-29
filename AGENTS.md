Talk to the user as a fellow senior developer.

AgentPool is a control plane, not an auto-router. Keep provider/model judgment with the primary agent.

Project guardrails:

- Treat the committed docs as the public source of truth, especially `README.md`,
  `docs/architecture.md`, `docs/onboarding.md`, `docs/mcp-tools.md`, and
  `docs/usage-detection.md`.
- Use Python 3.11+.
- Use tmux as the first runtime.
- Preserve explicit provider selection. Do not add `provider=auto`.
- Do not implement browser scraping, credential storage, model ranking, silent overage, silent merge, or silent push behavior.
- Add fake-provider coverage before relying on real provider adapters.
- Prefer worktree isolation for mutating tasks.
- Use subprocess argument arrays; avoid shell strings for untrusted input.
- Store sessions, events, usage snapshots, artifacts, and file leases in SQLite.
- Keep artifacts outside the repo by default under `~/.agentpool/artifacts`.

When AgentPool tools are available:

1. Call inventory or usage before delegating.
2. Choose provider/model/harness explicitly.
3. Prefer `read_only` isolation for exploration and review.
4. Use `worktree` isolation for edits.
5. Spawn narrow tasks.
6. Observe, steer, interrupt, collect, and terminate deliberately.
7. Do not merge or push worker changes without user approval.
