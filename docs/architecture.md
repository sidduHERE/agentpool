# Architecture

AgentPool v0.1 is a local control plane. It exposes inventory, usage snapshots, live session controls, and artifact collection without choosing providers for the primary agent.

Public source-of-truth docs:

- `README.md`: install, quickstart, provider matrix, privacy posture.
- `docs/onboarding.md`: human and MCP setup flow.
- `docs/mcp-tools.md`: MCP tools, resources, and prompts.
- `docs/usage-detection.md`: provider usage sources and confidence policy.
- `docs/provider-adapters.md`: provider launch and usage-probe adapter notes.

Core modules:

- `models.py`: Pydantic response and persistence models.
- `config.py`: `~/.agentpool/config.yaml` loader and defaults.
- `providers/`: conservative CLI detection and launch templates.
- `runtimes/tmux.py`: required v0.1 runtime.
- `store.py`: SQLite sessions, events, usage snapshots, artifacts, and leases.
- `session_manager.py`: policy enforcement and worker lifecycle orchestration.
- `mcp_server.py`: MCP tools and resources.
