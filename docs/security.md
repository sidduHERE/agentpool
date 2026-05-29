# Security

AgentPool is local-first and does not store provider credentials.

Guardrails:

- No provider tokens, passwords, browser cookies, or private session scraping.
- No silent paid overage.
- No `provider=auto` in v0.1.
- Raw key sending is disabled by default except the dedicated interrupt path.
- Worktree isolation is explicit by default. A local policy may require it for
  mutating roles, but AgentPool does not silently assume ownership of a user's
  worktree lifecycle.
- Subprocess calls use argument arrays.
- Observe/send output is redacted before persistence with conservative token,
  key, URI-password, JWT, and private-key patterns.

Live usage probes are explicit. `inventory` does not run provider TUIs or
network quota probes. `usage --refresh` and `usage-summary --refresh` may read
existing CLI auth state and contact provider APIs, depending on the provider.

Worker output is an untrusted channel. CLI and MCP observe/collect paths return
summary metadata by default; inline excerpts and transcript/event resources are
bounded and delimited. MCP lockdown mode (`agentpool mcp --lockdown` or
`AGENTPOOL_MCP_LOCKDOWN=1`) blocks transcript/event resources and marks raw
worker-text artifact paths as gated.

Run `agentpool doctor --privacy --json` to inspect the local storage paths,
optional usage backends, and provider source map for the installed environment.
