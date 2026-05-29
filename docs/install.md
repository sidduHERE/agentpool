# Install AgentPool

AgentPool is a local CLI and MCP server. Install it on the same machine where
your coding-agent CLIs are logged in and where `tmux` is available.

## Requirements

- Python 3.11 or newer.
- `tmux` on `PATH`.
- Git on `PATH`.
- macOS or Linux for the runtime. The `agentpool-cli` package installs on
  Windows too, but `tmux` is not native there, so Windows is supported only
  through WSL.

## Install From PyPI

The published distribution is `agentpool-cli`; the installed command is
`agentpool`.

```bash
uv tool install agentpool-cli      # recommended
pipx install agentpool-cli         # fallback
uvx agentpool-cli --help           # zero-install try
```

Use `python3.12` or newer if that is your available Python. Avoid the system
`python3` on machines where it is still Python 3.10; AgentPool requires Python
3.11 or newer. With `uv` you can pin the interpreter:

```bash
uv tool install --python python3.11 agentpool-cli
```

## Source Install

Install from a checkout:

```bash
git clone https://github.com/sidduHERE/agentpool.git
cd agentpool
uv tool install --force .
```

For local development:

```bash
uv venv
uv pip install -e ".[dev]"
```

If you want a plain virtual environment from source:

```bash
python3.11 -m venv ~/.local/agentpool-venv
~/.local/agentpool-venv/bin/python -m pip install --upgrade pip
~/.local/agentpool-venv/bin/python -m pip install .
~/.local/agentpool-venv/bin/agentpool --help
```

## GitHub Release Install

You can install a wheel pinned to a specific GitHub release without PyPI. The
installer script wraps the release download, verifies the release SHA256 digest
when GitHub exposes one, installs with `uv tool` when available (or `pipx`), and
runs basic post-install validation:

```bash
scripts/install.sh latest
```

Useful installer knobs:

```bash
AGENTPOOL_INSTALLER=pipx scripts/install.sh latest
AGENTPOOL_PYTHON=python3.12 scripts/install.sh latest
AGENTPOOL_SETUP_CLIENTS=codex,claude-code scripts/install.sh latest
```

Raw wheel install is still supported as a debugging fallback:

```bash
mkdir -p /tmp/agentpool-release
gh release download <tag> \
  --repo sidduHERE/agentpool \
  --pattern 'agentpool_cli-*-py3-none-any.whl' \
  --dir /tmp/agentpool-release
uv tool install --force --python python3.11 \
  /tmp/agentpool-release/agentpool_cli-*-py3-none-any.whl
agentpool --version
```

## First Five Minutes

You do not need any real provider configured to test the control plane:

```bash
agentpool init
agentpool setup cursor
agentpool config validate
agentpool doctor --deep --privacy
agentpool models validate
agentpool smoke --provider fake-question --repo . --json
```

Then inspect real provider availability without launching provider TUIs:

```bash
agentpool inventory --json
agentpool usage-summary --json
```

Run explicit live usage probes only when you want current usage data:

```bash
agentpool usage-summary --refresh --json
agentpool doctor --privacy --json
```

All worker control commands have machine-readable output:

```bash
agentpool send <session-id> "Continue." --json
agentpool interrupt <session-id> --json
agentpool attach <session-id> --json
agentpool transcript <session-id> --offset 0 --limit 4000 --json
agentpool terminate <session-id> --json
```

Use the guided setup summaries:

```bash
agentpool setup all
agentpool setup codex
```

Setup guides:
[Codex](setup-codex.md),
[Claude Code](setup-claude-code.md),
[Copilot](setup-copilot.md),
[Devin](setup-devin.md), and
[Droid](setup-droid.md).

## MCP Setup

Verified install (deeplink or one-liner shell command):

```bash
agentpool mcp-config --client cursor --absolute-command --install
agentpool mcp-config --client claude-code --absolute-command --install
agentpool mcp-config --client codex --absolute-command --install
agentpool mcp-config --client copilot-cli --absolute-command --install
```

See [MCP client setup](mcp-clients.md) for per-host verify steps and manual
config paste.

## Upgrades

For PyPI `uv tool` installs:

```bash
uv tool upgrade agentpool-cli
agentpool --version
```

For editable installs:

```bash
git pull
uv pip install -e ".[dev]"
```
