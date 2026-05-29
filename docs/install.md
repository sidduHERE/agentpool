# Install AgentPool

AgentPool is a local CLI and MCP server. Install it on the same machine where
your coding-agent CLIs are logged in and where `tmux` is available.

## Requirements

- Python 3.11 or newer.
- `tmux` on `PATH`.
- Git on `PATH`.
- macOS or Linux. Windows is not a v0.1 target except through WSL-like shells.

## Source Install

While the repository is in private preview, install from a checkout:

```bash
git clone git@github.com:sidduHERE/agentpool.git
cd agentpool
uv tool install --force .
```

For local development:

```bash
uv venv
uv pip install -e ".[dev]"
```

Use `python3.12` or newer if that is your available Python. Avoid the system
`python3` on machines where it is still Python 3.10; AgentPool requires Python
3.11 or newer.

## Private GitHub Release Install

Invited private-preview testers can use the installer script from a checkout.
It wraps the private GitHub release download, verifies the release SHA256 digest
when GitHub exposes one, installs with `uv tool` when available, and runs basic
post-install validation:

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

Direct GitHub release asset URLs return `404` to unauthenticated package
installers while the repository is private, so use `gh release download` or the
installer script for now.

## Public Package Path

The public PyPI distribution will be `agentpool-cli` because `agentpool` is
already used by another project. The console command stays `agentpool`.

Once public publishing is enabled:

```bash
uv tool install agentpool-cli
agentpool setup codex
agentpool doctor --deep --privacy
```

Fallback:

```bash
pipx install agentpool-cli
uvx agentpool-cli --help
```

If you want a plain virtual environment from source:

```bash
python3.11 -m venv ~/.local/agentpool-venv
~/.local/agentpool-venv/bin/python -m pip install --upgrade pip
~/.local/agentpool-venv/bin/python -m pip install .
~/.local/agentpool-venv/bin/agentpool --help
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

For public `uv tool` installs:

```bash
uv tool upgrade agentpool-cli
agentpool --version
```

For editable installs:

```bash
git pull
uv pip install -e ".[dev]"
```
