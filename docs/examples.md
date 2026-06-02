# Examples

Spawn a fake question worker:

```bash
agentpool spawn \
  --provider fake-question \
  --repo . \
  --task "Inspect the project and ask one clarifying question." \
  --isolation read_only
```

Observe and steer:

```bash
agentpool observe <session-id> --wait-for question,completed,error --timeout 120 --json
agentpool send <session-id> "Inspect migrations first. Stay read-only."
agentpool session show <session-id> --json
agentpool collect <session-id> --json
agentpool terminate <session-id> --dry-run --json
agentpool terminate <session-id>
```

Read task text from stdin:

```bash
cat task.md | agentpool spawn --provider <provider-id> --repo . --stdin --json
```

Run usage probes without provider TUI fallbacks:

```bash
AGENTPOOL_NO_INTERACTIVE_USAGE=1 agentpool usage-summary --refresh --json
agentpool usage --provider claude-code --no-interactive --json
```

Preview worktree cleanup before removing anything:

```bash
agentpool worktrees cleanup --session-id <session-id> --dry-run --json
agentpool worktrees cleanup --session-id <session-id> --force --json
```
