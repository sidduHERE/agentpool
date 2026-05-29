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
agentpool collect <session-id> --json
agentpool terminate <session-id>
```
