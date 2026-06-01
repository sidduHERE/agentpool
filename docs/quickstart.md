# AgentPool Quickstart

The shortest path from install to offloading work to another subscription.
AgentPool reads the live usage limits of every coding-agent subscription you
have so you can move work to whichever still has headroom. It is a control
plane, not a router: you pick the provider and model explicitly.

## 1. Verify the environment

```bash
agentpool init
agentpool doctor --deep --privacy
```

`doctor` confirms `tmux`, SQLite, and the artifact root, and lists each
provider's install/auth state.

## 2. Smoke test without a real provider

```bash
agentpool smoke --provider fake-question --repo . --json
```

A packaged fake provider runs the full spawn -> question -> send -> complete
cycle. `"ok": true` means the control plane works end to end.

## 3. See live provider and usage state

```bash
agentpool inventory --json
agentpool usage-summary --json          # add --refresh for live probes
```

## 4. Run a real worker (explicit provider + isolation)

```bash
agentpool spawn --provider <provider-id> --repo . \
  --task "Inspect this repo and ask one clarifying question." \
  --isolation read_only --json
```

The result includes a top-level `session_id`. Drive it with:

```bash
agentpool observe <session-id> --wait-for question,completed,error --timeout 120 --json
agentpool send <session-id> "Continue with the smallest useful check." --json
agentpool collect <session-id> --json
agentpool terminate <session-id> --json
```

Use `--isolation worktree` instead of `read_only` for tasks that edit files.

## Next

- Full agent guidance: `agentpool://skill.md`
- Setup and privacy detail: `agentpool://onboarding`
- User delegation preferences: `agentpool://preferences.md`
- MCP host config: `agentpool mcp-config --client <host> --absolute-command --install`
