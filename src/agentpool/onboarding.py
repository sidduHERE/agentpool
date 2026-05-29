from __future__ import annotations

import base64
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from agentpool.config import load_config
from agentpool.models import ObserveEvent, SessionState, SpawnWorkerRequest, ToolError
from agentpool.runtimes.tmux import TmuxRuntime
from agentpool.session_manager import SessionManager
from agentpool.store import Store
from agentpool.usage.probes import detect_ccusage, detect_codexbar


def init_config(path: Path, force: bool = False) -> dict[str, Any]:
    path = path.expanduser()
    existed = path.exists()
    backup_path: Path | None = None
    if existed and not force:
        config = load_config(path)
        return {
            "changed": False,
            "config_path": str(path),
            "reason": "config already exists",
            "providers": sorted(config.providers),
            "next_commands": default_onboarding_nudges(),
        }
    if existed:
        backup_path = path.with_suffix(path.suffix + ".bak")
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    return {
        "changed": True,
        "config_path": str(path),
        "backup_path": str(backup_path) if backup_path else None,
        "providers": sorted(config.providers),
        "next_commands": default_onboarding_nudges(),
    }


CURSOR_SETUP_NUDGE = "agentpool setup cursor"
CURSOR_INSTALL_NUDGE = "agentpool mcp-config --client cursor --absolute-command --install"


def default_onboarding_nudges() -> list[str]:
    return [CURSOR_SETUP_NUDGE]


MCP_CLIENTS = {
    "generic",
    "claude-code",
    "claude-desktop",
    "codex",
    "cursor",
    "copilot-cli",
}


SETUP_TARGETS = {
    "codex": {
        "provider_id": "codex-cli",
        "mcp_client": "codex",
        "display_name": "Codex CLI",
        "usage_backend": "native",
        "install_hint": "Install Codex CLI, for example `npm install -g @openai/codex`, then confirm `codex --version` works.",
        "login_hint": "Run `codex` and complete the provider login/trust flow outside AgentPool.",
        "setup_doc": "docs/setup-codex.md",
        "manual_steps": [
            "Install Codex CLI if provider_installed is false.",
            "Log in with Codex CLI yourself if usage_probe reports unauthenticated or unavailable.",
            "Paste the MCP config into ~/.codex/config.toml or project .codex/config.toml.",
            "Open Codex and run /mcp to confirm AgentPool is connected.",
        ],
    },
    "codex-cli": {
        "provider_id": "codex-cli",
        "mcp_client": "codex",
        "display_name": "Codex CLI",
        "usage_backend": "native",
        "install_hint": "Install Codex CLI, for example `npm install -g @openai/codex`, then confirm `codex --version` works.",
        "login_hint": "Run `codex` and complete the provider login/trust flow outside AgentPool.",
        "setup_doc": "docs/setup-codex.md",
        "manual_steps": [
            "Install Codex CLI if provider_installed is false.",
            "Log in with Codex CLI yourself if usage_probe reports unauthenticated or unavailable.",
            "Paste the MCP config into ~/.codex/config.toml or project .codex/config.toml.",
            "Open Codex and run /mcp to confirm AgentPool is connected.",
        ],
    },
    "claude": {
        "provider_id": "claude-code",
        "mcp_client": "claude-code",
        "display_name": "Claude Code",
        "usage_backend": "native",
        "install_hint": "Install Claude Code, then confirm `claude --version` works.",
        "login_hint": "Run `claude` and complete the provider login flow outside AgentPool.",
        "setup_doc": "docs/setup-claude-code.md",
        "manual_steps": [
            "Install Claude Code if provider_installed is false.",
            "Log in with Claude Code yourself if usage_probe reports unauthenticated or unavailable.",
            "Use the generated Claude Code MCP command or .mcp.json config.",
        ],
    },
    "claude-code": {
        "provider_id": "claude-code",
        "mcp_client": "claude-code",
        "display_name": "Claude Code",
        "usage_backend": "native",
        "install_hint": "Install Claude Code, then confirm `claude --version` works.",
        "login_hint": "Run `claude` and complete the provider login flow outside AgentPool.",
        "setup_doc": "docs/setup-claude-code.md",
        "manual_steps": [
            "Install Claude Code if provider_installed is false.",
            "Log in with Claude Code yourself if usage_probe reports unauthenticated or unavailable.",
            "Use the generated Claude Code MCP command or .mcp.json config.",
        ],
    },
    "copilot": {
        "provider_id": "copilot-cli",
        "mcp_client": None,
        "display_name": "GitHub Copilot CLI",
        "usage_backend": "native",
        "install_hint": "Install GitHub CLI and the Copilot extension, then confirm `gh copilot --help` works.",
        "login_hint": "Run `gh auth login`; AgentPool only uses ambient GitHub auth for explicit usage refresh.",
        "setup_doc": "docs/setup-copilot.md",
        "manual_steps": [
            "Install GitHub CLI and the Copilot extension if provider_installed is false.",
            "Run gh auth login yourself if usage_probe reports unauthenticated or unavailable.",
        ],
    },
    "copilot-cli": {
        "provider_id": "copilot-cli",
        "mcp_client": None,
        "display_name": "GitHub Copilot CLI",
        "usage_backend": "native",
        "install_hint": "Install GitHub CLI and the Copilot extension, then confirm `gh copilot --help` works.",
        "login_hint": "Run `gh auth login`; AgentPool only uses ambient GitHub auth for explicit usage refresh.",
        "setup_doc": "docs/setup-copilot.md",
        "manual_steps": [
            "Install GitHub CLI and the Copilot extension if provider_installed is false.",
            "Run gh auth login yourself if usage_probe reports unauthenticated or unavailable.",
        ],
    },
    "devin": {
        "provider_id": "devin-cli",
        "mcp_client": None,
        "display_name": "Devin CLI",
        "usage_backend": "native",
        "install_hint": "Install Devin CLI, then confirm `devin --version` works.",
        "login_hint": "Run `devin` and complete the provider login flow outside AgentPool.",
        "setup_doc": "docs/setup-devin.md",
        "manual_steps": [
            "Install Devin CLI if provider_installed is false.",
            "Log in with Devin CLI yourself if usage_probe reports unauthenticated or unavailable.",
        ],
    },
    "devin-cli": {
        "provider_id": "devin-cli",
        "mcp_client": None,
        "display_name": "Devin CLI",
        "usage_backend": "native",
        "install_hint": "Install Devin CLI, then confirm `devin --version` works.",
        "login_hint": "Run `devin` and complete the provider login flow outside AgentPool.",
        "setup_doc": "docs/setup-devin.md",
        "manual_steps": [
            "Install Devin CLI if provider_installed is false.",
            "Log in with Devin CLI yourself if usage_probe reports unauthenticated or unavailable.",
        ],
    },
    "cursor-cli": {
        "provider_id": "cursor-cli",
        "mcp_client": None,
        "display_name": "Cursor Agent CLI",
        "usage_backend": "codexbar",
        "install_hint": "Install Cursor Agent CLI, for example `curl https://cursor.com/install -fsS | bash`, then confirm `agent --version` or `cursor-agent --version` works.",
        "login_hint": "Run `agent login` and confirm `agent status --format json` reports authenticated.",
        "setup_doc": "docs/setup-cursor-cli.md",
        "manual_steps": [
            "Install Cursor Agent CLI if provider_installed is false.",
            "Run agent login yourself if auth_probe reports unauthenticated or unavailable.",
            "Run agent models to see whether Cursor exposes account-specific model slugs.",
            "Use `agentpool usage --provider cursor-cli --backend codexbar --json` for optional Cursor usage if CodexBar is installed.",
        ],
    },
    "cursor": {
        "kind": "host",
        "mcp_client": "cursor",
        "display_name": "Cursor",
        "setup_doc": "docs/setup-cursor.md",
        "manual_steps": [
            "Run `agentpool mcp-config --client cursor --absolute-command --install`.",
            "Click the Cursor deeplink or paste `.cursor/mcp.json` from the generated config.",
            "Open Cursor Settings > MCP and confirm agentpool tools are visible.",
        ],
    },
    "droid": {
        "provider_id": "droid-cli",
        "mcp_client": None,
        "display_name": "Droid CLI",
        "usage_backend": "native",
        "install_hint": "Install Factory Droid CLI, then confirm `droid --version` works.",
        "login_hint": "Run `droid` and complete the provider login flow outside AgentPool.",
        "setup_doc": "docs/setup-droid.md",
        "manual_steps": [
            "Install Droid CLI if provider_installed is false.",
            "Log in with Droid yourself if usage_probe reports unauthenticated or unavailable.",
            "Wire AgentPool into Cursor or another MCP host before spawning Droid workers.",
        ],
    },
    "droid-cli": {
        "provider_id": "droid-cli",
        "mcp_client": None,
        "display_name": "Droid CLI",
        "usage_backend": "native",
        "install_hint": "Install Factory Droid CLI, then confirm `droid --version` works.",
        "login_hint": "Run `droid` and complete the provider login flow outside AgentPool.",
        "setup_doc": "docs/setup-droid.md",
        "manual_steps": [
            "Install Droid CLI if provider_installed is false.",
            "Log in with Droid yourself if usage_probe reports unauthenticated or unavailable.",
            "Wire AgentPool into Cursor or another MCP host before spawning Droid workers.",
        ],
    },
}

SETUP_ALL_TARGETS = ["codex", "claude-code", "copilot-cli", "devin-cli", "droid-cli", "cursor-cli"]


def mcp_host_config(command: str | None = None) -> dict[str, Any]:
    return {"mcpServers": {"agentpool": _stdio_server_config(command)}}


def mcp_client_config(client: str = "generic", command: str | None = None) -> dict[str, Any]:
    """Return copy/pasteable MCP setup for common local MCP hosts."""
    normalized = client.strip().lower()
    if normalized not in MCP_CLIENTS:
        return {
            "client": client,
            "ok": False,
            "error": f"unknown MCP client {client!r}",
            "supported_clients": sorted(MCP_CLIENTS),
        }

    server = _stdio_server_config(command)
    if normalized == "codex":
        result: dict[str, Any] = {
            "client": normalized,
            "ok": True,
            "path": "~/.codex/config.toml or .codex/config.toml",
            "format": "toml",
            "config": _codex_mcp_toml(server),
            "commands": [_codex_mcp_add_command(server)],
            "verify": ["codex mcp list", "open Codex TUI and run /mcp"],
        }
    elif normalized == "cursor":
        result = {
            "client": normalized,
            "ok": True,
            "path": ".cursor/mcp.json or ~/.cursor/mcp.json",
            "format": "json",
            "config": {"mcpServers": {"agentpool": {"type": "stdio", **server}}},
            "verify": ["Cursor Settings > MCP", "restart Cursor if tools do not appear"],
        }
    elif normalized == "claude-code":
        result = {
            "client": normalized,
            "ok": True,
            "path": ".mcp.json for project scope, or ~/.claude.json for user scope",
            "format": "json",
            "config": {"mcpServers": {"agentpool": {"type": "stdio", **server, "env": {}}}},
            "commands": _claude_mcp_add_commands(server),
            "verify": ["claude mcp list", "open Claude Code and run /mcp"],
        }
    elif normalized == "copilot-cli":
        result = {
            "client": normalized,
            "ok": True,
            "path": "~/.copilot/mcp-config.json",
            "format": "json",
            "config": {
                "mcpServers": {
                    "agentpool": {
                        "type": "local",
                        **server,
                        "tools": ["*"],
                    }
                }
            },
            "commands": [_copilot_mcp_add_command(server)],
            "verify": ["copilot mcp list", "copilot mcp get agentpool", "/mcp show agentpool inside Copilot CLI"],
        }
    elif normalized == "claude-desktop":
        result = {
            "client": normalized,
            "ok": True,
            "path": "~/Library/Application Support/Claude/claude_desktop_config.json",
            "format": "json",
            "config": {"mcpServers": {"agentpool": server}},
            "verify": ["restart Claude Desktop", "check MCP/server settings"],
        }
    else:
        result = {
            "client": normalized,
            "ok": True,
            "path": "host-specific MCP config file",
            "format": "json",
            "config": {"mcpServers": {"agentpool": server}},
            "verify": ["restart or reload the MCP host", "confirm agentpool tools/resources are visible"],
        }
    _attach_mcp_install(normalized, server, result)
    return result


def _stdio_server_config(command: str | None = None) -> dict[str, Any]:
    return {"command": command or "agentpool", "args": ["mcp"]}


def _codex_mcp_toml(server: dict[str, Any]) -> str:
    args = ", ".join(json.dumps(arg) for arg in server.get("args", []))
    command = json.dumps(server["command"])
    return (
        "[mcp_servers.agentpool]\n"
        f"command = {command}\n"
        f"args = [{args}]\n"
        "startup_timeout_sec = 10\n"
        "tool_timeout_sec = 300\n"
    )


def _stdio_server_invocation(server: dict[str, Any]) -> str:
    args = server.get("args") or []
    if not args:
        return str(server["command"])
    return f"{server['command']} {' '.join(args)}"


def _codex_mcp_add_command(server: dict[str, Any]) -> str:
    return f"codex mcp add agentpool -- {_stdio_server_invocation(server)}"


def _claude_mcp_add_commands(server: dict[str, Any]) -> list[str]:
    invocation = _stdio_server_invocation(server)
    return [
        f"claude mcp add --transport stdio --scope local agentpool -- {invocation}",
        f"claude mcp add --transport stdio --scope project agentpool -- {invocation}",
        f"claude mcp add --transport stdio --scope user agentpool -- {invocation}",
    ]


def _copilot_mcp_add_command(server: dict[str, Any]) -> str:
    return f"copilot mcp add agentpool -- {_stdio_server_invocation(server)}"


def _cursor_mcp_install_deeplink(server: dict[str, Any], name: str = "agentpool") -> str:
    transport = json.dumps(
        {"command": server["command"], "args": server.get("args", [])},
        separators=(",", ":"),
    )
    encoded = base64.b64encode(transport.encode("utf-8")).decode("ascii")
    return (
        "cursor://anysphere.cursor-deeplink/mcp/install?"
        f"name={quote(name)}&config={quote(encoded, safe='')}"
    )


def _attach_mcp_install(client: str, server: dict[str, Any], payload: dict[str, Any]) -> None:
    install: dict[str, Any] = {"client": client}
    if client == "cursor":
        install["kind"] = "deeplink"
        install["deeplink"] = _cursor_mcp_install_deeplink(server)
        install["instructions"] = [
            "Click the deeplink or paste it into your browser.",
            "Cursor prompts to install the MCP server.",
        ]
    elif client == "claude-code":
        install["kind"] = "shell"
        install["commands"] = payload.get("commands") or _claude_mcp_add_commands(server)
        install["instructions"] = [
            "Run one shell command below.",
            "`--scope local` is personal to the current project (Claude default).",
            "`--scope project` writes team-shared `.mcp.json`. `--scope user` writes `~/.claude.json`.",
            "Verify with `claude mcp list` and `/mcp` inside Claude Code.",
        ]
    elif client == "codex":
        install["kind"] = "shell"
        install["commands"] = payload.get("commands") or [_codex_mcp_add_command(server)]
        install["instructions"] = [
            "Run the shell command below, or paste the TOML block into ~/.codex/config.toml.",
            "Verify with `codex mcp list` and `/mcp` inside Codex.",
        ]
    elif client == "copilot-cli":
        install["kind"] = "shell"
        install["commands"] = payload.get("commands") or [_copilot_mcp_add_command(server)]
        install["instructions"] = [
            "Run the shell command below (writes ~/.copilot/mcp-config.json).",
            "Or use interactive `/mcp add` inside Copilot CLI with the same STDIO command.",
            "Verify with `copilot mcp list` or `/mcp show agentpool`.",
        ]
        install["tui"] = {
            "command": "/mcp add",
            "fields": {
                "Server Name": "agentpool",
                "Server Type": "STDIO",
                "Command": _stdio_server_invocation(server),
                "Tools": "*",
            },
        }
    else:
        install["kind"] = "manual"
        install["instructions"] = [
            f"Paste the generated config into {payload.get('path', 'the host MCP config file')}.",
            "Restart or reload the MCP host if tools do not appear.",
        ]
    payload["install"] = install


def format_mcp_install(data: dict[str, Any]) -> str:
    install = data.get("install")
    if not isinstance(install, dict):
        return ""

    lines = [f"MCP install helper for {install.get('client', data.get('client', 'unknown'))}:"]
    kind = install.get("kind")
    if kind == "deeplink" and install.get("deeplink"):
        lines.extend(["", "One-click install (Cursor deeplink):", str(install["deeplink"])])
    if install.get("commands"):
        lines.extend(["", "Run one of these commands:"])
        lines.extend(str(command) for command in install["commands"])
    tui = install.get("tui")
    if isinstance(tui, dict):
        lines.extend(["", f"Copilot CLI interactive setup ({tui.get('command', '/mcp add')}):"])
        fields = tui.get("fields") or {}
        for label, value in fields.items():
            lines.append(f"  {label}: {value}")
    if install.get("instructions"):
        lines.extend(["", "Steps:"])
        lines.extend(f"  - {step}" for step in install["instructions"])
    return "\n".join(lines)


def deep_doctor(manager: SessionManager) -> dict[str, Any]:
    checks = [
        _check_tmux_roundtrip(manager.config.runtime.tmux.session_prefix),
        _check_sqlite(manager.store),
        _check_artifact_root(manager.config.storage.artifacts),
        _check_usage_cache(manager),
        _check_codexbar(),
    ]
    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "inventory": manager.inventory(include_usage=False),
        "next_commands": default_onboarding_nudges(),
    }


def privacy_doctor(manager: SessionManager) -> dict[str, Any]:
    return {
        "credential_storage": {
            "agentpool_stores_provider_credentials": False,
            "browser_scraping_enabled_by_default": False,
            "browser_cookie_sources_enabled_by_default": False,
            "macos_keychain_access_by_agentpool": False,
        },
        "local_storage": {
            "config_path": str(Path("~/.agentpool/config.yaml").expanduser()),
            "sqlite_db": str(manager.config.storage.db),
            "artifact_root": str(manager.config.storage.artifacts),
            "stores": [
                "sessions",
                "events",
                "usage snapshots",
                "artifact manifests",
                "advisory file leases",
            ],
        },
        "usage_refresh": {
            "inventory_runs_live_usage_probes": False,
            "live_usage_requires_explicit_refresh": True,
            "default_backend": "combined",
            "optional_backends": {
                "codexbar": detect_codexbar(),
                "ccusage": detect_ccusage(),
            },
        },
        "usage_sources": [
            {
                "provider_id": "codex-cli",
                "source": "Codex local app-server RPC",
                "reads_existing_auth_state": True,
                "network": "local app-server",
            },
            {
                "provider_id": "claude-code",
                "source": "temporary Claude Code tmux session with /usage",
                "reads_existing_auth_state": True,
                "network": "provider CLI decides",
            },
            {
                "provider_id": "devin-cli",
                "source": "existing Devin CLI credentials in memory and plan-status API",
                "reads_existing_auth_state": True,
                "network": "Devin/Windsurf API on explicit refresh",
            },
            {
                "provider_id": "copilot-cli",
                "source": "ambient GitHub token from env or gh auth token",
                "reads_existing_auth_state": True,
                "network": "GitHub Copilot API on explicit refresh",
            },
            {
                "provider_id": "cursor-cli",
                "source": "Cursor Agent CLI status/about and optional CodexBar Cursor usage",
                "reads_existing_auth_state": True,
                "network": "Cursor CLI or CodexBar source dependent on explicit refresh",
            },
            {
                "provider_id": "codexbar",
                "source": "optional external CodexBar CLI safe sources",
                "reads_existing_auth_state": True,
                "network": "CodexBar source dependent",
            },
            {
                "provider_id": "ccusage",
                "source": "optional local Claude Code usage logs",
                "reads_existing_auth_state": False,
                "network": "offline command only",
            },
        ],
        "redaction": {
            "applied_before_persistence": True,
            "patterns": [
                "authorization headers",
                "token/key/secret/password assignments",
                "provider API keys",
                "GitHub tokens",
                "Slack tokens",
                "AWS access key ids",
                "JWT-shaped strings",
                "URI passwords",
                "private key blocks",
            ],
        },
    }


def setup_provider(
    manager: SessionManager,
    target: str,
    *,
    backend: str | None = None,
    run_usage: bool = True,
    absolute_command: bool = True,
) -> dict[str, Any]:
    normalized = target.strip().lower()
    setup = SETUP_TARGETS.get(normalized)
    if setup is None:
        return {
            "ok": False,
            "target": target,
            "error": f"unknown setup target {target!r}",
            "supported_targets": sorted(SETUP_TARGETS),
            "action": "Run `agentpool setup all` to inspect supported providers, or choose one of the supported targets.",
            "checks": [],
        }

    if setup.get("kind") == "host":
        return _setup_host(normalized, setup, absolute_command=absolute_command)

    provider_id = str(setup["provider_id"])
    usage_backend = backend or str(setup["usage_backend"])
    checks: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "target": normalized,
        "provider_id": provider_id,
        "display_name": setup["display_name"],
        "usage_backend": usage_backend,
        "does_not": [
            "store provider credentials",
            "log in to provider CLIs",
            "scrape browser dashboards",
            "edit MCP host config files",
        ],
    }

    provider = _provider_descriptor(manager, provider_id)
    configured = provider is not None
    checks.append(
        {
            "name": "provider_configured",
            "ok": configured,
            "message": "Provider is configured." if configured else f"Provider {provider_id} is not configured.",
            "action": None
            if configured
            else f"Run `agentpool init` or add `{provider_id}` to the `providers` section in your AgentPool config.",
        }
    )
    if provider is None:
        result["checks"] = checks
        result["manual_steps"] = setup["manual_steps"]
        result["actions"] = _setup_actions(checks)
        result["ok"] = False
        return result

    result["provider"] = provider
    installed = bool(provider.get("installed"))
    checks.append(
        {
            "name": "provider_installed",
            "ok": installed,
            "message": provider.get("binary_path") or "Provider binary was not found on PATH.",
            "version": provider.get("version"),
            "action": None if installed else str(setup["install_hint"]),
        }
    )
    auth = provider.get("auth") or {}
    auth_ok = auth.get("status") in {"authenticated", "unknown"}
    checks.append(
        {
            "name": "auth_probe",
            "ok": auth_ok,
            "message": auth.get("reason") or f"Auth status: {auth.get('status', 'unknown')}.",
            "status": auth.get("status"),
            "confidence": auth.get("confidence"),
            "action": None if auth_ok else str(setup["login_hint"]),
        }
    )

    try:
        models = manager.provider_models(provider_id)["providers"][0]
        result["models"] = models
        checks.append(
            {
                "name": "model_catalog",
                "ok": bool(models.get("models")),
                "message": f"default={models.get('default_model') or 'unset'}, models={len(models.get('models') or [])}",
                "action": None
                if models.get("models")
                else (
                    f"Review `agentpool models --provider {provider_id}` and "
                    "`agentpool models validate --path src/agentpool/provider_model_catalog.json`."
                ),
            }
        )
    except Exception as exc:
        checks.append(
            {
                "name": "model_catalog",
                "ok": False,
                "message": str(exc),
                "action": "Validate the model catalog, then rerun setup.",
            }
        )

    if run_usage:
        try:
            usage = manager.usage_snapshot(provider_id, backend=usage_backend)
            snapshots = usage.get("snapshots") or []
            snapshot = snapshots[0] if snapshots else {}
            result["usage"] = usage
            checks.append(_usage_check(snapshot))
            try:
                result["capacity_summary"] = manager.usage_summary(provider_id=provider_id, refresh=False)
            except Exception as exc:
                checks.append(
                    {
                        "name": "capacity_summary",
                        "ok": False,
                        "message": str(exc),
                        "action": f"Run `agentpool usage-summary --provider {provider_id} --refresh --json` for the raw usage response.",
                    }
                )
        except Exception as exc:
            checks.append(
                {
                    "name": "usage_probe",
                    "ok": False,
                    "message": str(exc),
                    "backend": usage_backend,
                    "action": (
                        f"Run `agentpool usage-summary --provider {provider_id} --refresh --json`; "
                        f"if this is only setup validation, rerun `agentpool setup {normalized} --skip-usage`."
                    ),
                }
            )
    else:
        checks.append(
            {
                "name": "usage_probe",
                "ok": None,
                "message": "Skipped by --skip-usage.",
                "backend": usage_backend,
                "action": f"Run `agentpool usage-summary --provider {provider_id} --refresh --json` when you want live usage.",
            }
        )

    mcp_client = setup.get("mcp_client")
    if mcp_client:
        mcp_config = mcp_client_config(str(mcp_client), command_path(absolute=absolute_command))
        result["mcp_config"] = mcp_config
        checks.append(
            {
                "name": "mcp_config",
                "ok": bool(mcp_config.get("ok")),
                "message": f"Generated {mcp_config.get('format')} config for {mcp_config.get('path')}.",
                "action": "Run the generated install command or deeplink, or paste the MCP config manually.",
            }
        )
    else:
        checks.append(
            {
                "name": "mcp_config",
                "ok": None,
                "message": "No provider-specific MCP host config for this target.",
                "action": "Add AgentPool to your MCP host with `agentpool mcp-config --client <client> --absolute-command` if needed.",
            }
        )

    result["checks"] = checks
    result["manual_steps"] = setup["manual_steps"]
    result["setup_doc"] = setup.get("setup_doc")
    result["actions"] = _setup_actions(checks)
    result["next_commands"] = _setup_next_commands(provider_id, normalized, setup)
    result["ok"] = all(check["ok"] is not False for check in checks)
    return result


def _setup_host(
    target: str,
    setup: dict[str, Any],
    *,
    absolute_command: bool = True,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    command = command_path(absolute=absolute_command)
    installed = shutil.which("agentpool") is not None or Path(command).exists()
    checks.append(
        {
            "name": "agentpool_installed",
            "ok": installed,
            "message": command,
            "action": None
            if installed
            else "Install AgentPool with pipx or `uv pip install -e '.[dev]'`, then rerun setup.",
        }
    )

    mcp_client = str(setup["mcp_client"])
    mcp_config = mcp_client_config(mcp_client, command if absolute_command else "agentpool")
    checks.append(
        {
            "name": "mcp_config",
            "ok": bool(mcp_config.get("ok")),
            "message": f"Generated {mcp_config.get('format')} install helper for {mcp_config.get('path')}.",
            "action": "Run the generated install command or deeplink, or paste the MCP config manually.",
        }
    )

    result: dict[str, Any] = {
        "target": target,
        "kind": "host",
        "provider_id": None,
        "display_name": setup["display_name"],
        "does_not": [
            "store provider credentials",
            "log in to provider CLIs",
            "scrape browser dashboards",
            "edit MCP host config files",
        ],
        "mcp_config": mcp_config,
        "checks": checks,
        "manual_steps": setup["manual_steps"],
        "setup_doc": setup.get("setup_doc"),
        "actions": _setup_actions(checks),
        "next_commands": _setup_host_next_commands(target),
    }
    result["ok"] = all(check["ok"] is not False for check in checks)
    return result


def setup_all_providers(
    manager: SessionManager,
    *,
    backend: str | None = None,
    run_usage: bool = True,
    absolute_command: bool = True,
) -> dict[str, Any]:
    results = [
        setup_provider(
            manager,
            target,
            backend=backend,
            run_usage=run_usage,
            absolute_command=absolute_command,
        )
        for target in SETUP_ALL_TARGETS
    ]
    rows = []
    for result in results:
        checks = {check["name"]: check for check in result.get("checks", [])}
        usage = checks.get("usage_probe") or {}
        installed = checks.get("provider_installed") or {}
        rows.append(
            {
                "target": result["target"],
                "provider_id": result["provider_id"],
                "display_name": result["display_name"],
                "ok": result["ok"],
                "installed": installed.get("ok"),
                "binary": installed.get("message"),
                "usage_status": usage.get("status"),
                "usage_ok": usage.get("ok"),
                "usage_message": usage.get("message"),
                "mcp_config": "yes" if result.get("mcp_config") else "n/a",
                "action": _first_setup_action(result),
            }
        )
    return {
        "ok": all(row["ok"] for row in rows),
        "targets": SETUP_ALL_TARGETS,
        "rows": rows,
        "results": results,
        "does_not": [
            "store provider credentials",
            "log in to provider CLIs",
            "scrape browser dashboards",
            "edit MCP host config files",
        ],
    }


def run_fake_smoke(manager: SessionManager, repo: Path, provider_id: str = "fake-question") -> dict[str, Any]:
    result = manager.spawn_worker(
        SpawnWorkerRequest(
            provider_id=provider_id,
            task="AgentPool smoke test. Ask one question, then finish after steering.",
            repo_path=str(repo),
            isolation="read_only",
        )
    )
    session_id = result["session"]["id"]
    output: dict[str, Any] = {"session_id": session_id, "provider_id": provider_id}
    try:
        observed = manager.observe_worker(session_id, wait_for=["question"], timeout_seconds=8)
        output["question_event"] = observed.event.value
        output["question_state"] = observed.state.value
        output["send"] = manager.send_worker_message(session_id, "Continue read-only.")["ok"]
        done = manager.observe_worker(session_id, wait_for=["completed"], timeout_seconds=8)
        output["completed_event"] = done.event.value
        output["completed_state"] = done.state.value
        collected = manager.collect_worker_artifacts(session_id, mark_completed=True)
        output["artifact_dir"] = collected["artifact_dir"]
        output["artifact_kinds"] = [artifact["kind"] for artifact in collected["artifacts"]]
        output["git_dirty"] = collected["git"]["dirty"]
        output["ok"] = observed.event == ObserveEvent.QUESTION and done.event == ObserveEvent.COMPLETED
        return output
    finally:
        session = manager.store.get_session(session_id)
        if session and session.tmux and manager.runtime.exists(session.tmux):
            manager.terminate_worker(session_id, reason="smoke cleanup")


def run_real_read_only_smoke(
    manager: SessionManager,
    repo: Path,
    provider_id: str,
    model: str | None = None,
    timeout_seconds: int = 60,
    accept_startup_trust: bool = True,
) -> dict[str, Any]:
    if provider_id.startswith("fake-"):
        return run_fake_smoke(manager, repo=repo, provider_id=provider_id)
    selected_model = model or _smoke_model(manager, provider_id)
    result = manager.spawn_worker(
        SpawnWorkerRequest(
            provider_id=provider_id,
            task=_real_read_only_smoke_task(),
            repo_path=str(repo),
            isolation="read_only",
            role="reviewer",
            model=selected_model,
            initial_prompt_mode="arg",
        )
    )
    session_id = result["session"]["id"]
    output: dict[str, Any] = {
        "session_id": session_id,
        "provider_id": provider_id,
        "model": selected_model,
        "isolation": "read_only",
        "attach": manager.attach_info(session_id),
        "lifecycle": {"spawn": True},
    }
    final_observe = None
    try:
        first = _safe_observe(
            manager,
            session_id,
            wait_for=["question", "approval", "completed", "error"],
            timeout_seconds=min(timeout_seconds, 30),
            output=output,
        )
        if first is None:
            _collect_smoke_artifacts(manager, session_id, output, final_observe)
            output["ok"] = False
            return output
        output["initial_event"] = first.event.value
        output["initial_state"] = first.state.value
        final_observe = first
        startup_approval_steps = 0
        while first.event == ObserveEvent.APPROVAL_PROMPT and startup_approval_steps < 4:
            startup_approval_steps += 1
            if accept_startup_trust and _is_startup_trust_prompt(first.screen_excerpt or ""):
                output["lifecycle"]["startup_trust_prompt"] = "accepted"
                manager.send_worker_message(session_id, _startup_trust_message(provider_id))
                time.sleep(0.5)
                first = _safe_observe(
                    manager,
                    session_id,
                    wait_for=["question", "approval", "completed", "error"],
                    timeout_seconds=min(timeout_seconds, 30),
                    output=output,
                )
                if first is None:
                    _collect_smoke_artifacts(manager, session_id, output, final_observe)
                    output["ok"] = False
                    return output
                output["post_trust_event"] = first.event.value
                output["post_trust_state"] = first.state.value
                final_observe = first
                continue
            elif _is_startup_update_prompt(first.screen_excerpt or ""):
                output["lifecycle"]["startup_update_prompt"] = "skipped"
                manager.send_worker_message(session_id, "2")
                time.sleep(0.5)
                first = _safe_observe(
                    manager,
                    session_id,
                    wait_for=["question", "approval", "completed", "error"],
                    timeout_seconds=min(timeout_seconds, 30),
                    output=output,
                )
                if first is None:
                    _collect_smoke_artifacts(manager, session_id, output, final_observe)
                    output["ok"] = False
                    return output
                output["post_update_event"] = first.event.value
                output["post_update_state"] = first.state.value
                final_observe = first
                continue
            elif _is_startup_hook_prompt(first.screen_excerpt or ""):
                output["lifecycle"]["startup_hook_prompt"] = "continued_without_trusting"
                manager.send_worker_message(session_id, "3")
                time.sleep(0.5)
                first = _safe_observe(
                    manager,
                    session_id,
                    wait_for=["question", "approval", "completed", "error"],
                    timeout_seconds=min(timeout_seconds, 30),
                    output=output,
                )
                if first is None:
                    _collect_smoke_artifacts(manager, session_id, output, final_observe)
                    output["ok"] = False
                    return output
                output["post_hook_event"] = first.event.value
                output["post_hook_state"] = first.state.value
                final_observe = first
                continue
            else:
                output["blocked_reason"] = "approval prompt requires human review"
                break
        if first.event == ObserveEvent.APPROVAL_PROMPT and "blocked_reason" not in output:
            output["blocked_reason"] = "startup approval prompt did not clear"
        if "blocked_reason" not in output and final_observe and final_observe.event != ObserveEvent.ERROR:
            output["send"] = _send_smoke_message(manager, session_id, provider_id)
            output["lifecycle"]["send"] = True
            final_observe = _safe_observe(
                manager,
                session_id,
                wait_for=["completed", "question", "approval", "error"],
                timeout_seconds=timeout_seconds,
                output=output,
            )
            if final_observe is None:
                _collect_smoke_artifacts(manager, session_id, output, final_observe)
                output["ok"] = False
                return output
            output["final_event"] = final_observe.event.value
            output["final_state"] = final_observe.state.value
            output["completion_verified"] = final_observe.event == ObserveEvent.COMPLETED
        if final_observe and final_observe.state not in {SessionState.COMPLETED, SessionState.FAILED}:
            output["interrupt"] = manager.interrupt_worker(session_id)["ok"]
            output["lifecycle"]["interrupt"] = True
        else:
            output["interrupt"] = "skipped_final_state"
        _collect_smoke_artifacts(manager, session_id, output, final_observe)
        output["ok"] = _real_smoke_ok(output, final_observe)
        return output
    finally:
        session = manager.store.get_session(session_id)
        if session and session.tmux and manager.runtime.exists(session.tmux):
            terminated = manager.terminate_worker(session_id, reason="real read-only smoke cleanup")
            output["lifecycle"]["terminate"] = terminated["ok"]


def _check_tmux_roundtrip(prefix: str) -> dict[str, Any]:
    runtime = TmuxRuntime()
    if not runtime.tmux_binary:
        return {"name": "tmux_roundtrip", "ok": False, "reason": "tmux not found"}
    session_name = f"{prefix}-doctor-{int(time.time() * 1000) % 100000}"
    with tempfile.TemporaryDirectory(prefix="agentpool-doctor-") as tmp:
        try:
            ref = runtime.spawn(
                [
                    sys.executable,
                    "-c",
                    "import time; print('agentpool doctor ok', flush=True); time.sleep(2)",
                ],
                Path(tmp),
                {},
                session_name,
            )
        except Exception as exc:
            return {"name": "tmux_roundtrip", "ok": False, "reason": str(exc)}
        try:
            deadline = time.monotonic() + 3
            captured = ""
            while time.monotonic() < deadline:
                try:
                    captured = runtime.capture(ref, 20)
                except Exception as exc:
                    return {"name": "tmux_roundtrip", "ok": False, "reason": str(exc)}
                if "agentpool doctor ok" in captured:
                    break
                time.sleep(0.2)
            return {"name": "tmux_roundtrip", "ok": "agentpool doctor ok" in captured}
        finally:
            if runtime.exists(ref):
                runtime.terminate(ref)


def _check_sqlite(store: Store) -> dict[str, Any]:
    try:
        with store.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"name": "sqlite", "ok": True, "path": str(store.db_path)}
    except Exception as exc:
        return {"name": "sqlite", "ok": False, "path": str(store.db_path), "reason": str(exc)}


def _check_artifact_root(path: Path) -> dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".agentpool-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"name": "artifact_root", "ok": True, "path": str(path)}
    except Exception as exc:
        return {"name": "artifact_root", "ok": False, "path": str(path), "reason": str(exc)}


def _check_usage_cache(manager: SessionManager) -> dict[str, Any]:
    try:
        manager.cached_usage_snapshot()
        return {"name": "usage_cache", "ok": True}
    except Exception as exc:
        return {"name": "usage_cache", "ok": False, "reason": str(exc)}


def _check_codexbar() -> dict[str, Any]:
    info = detect_codexbar()
    return {"name": "codexbar", "ok": True, **info}


def command_path(absolute: bool = False) -> str:
    if absolute:
        return str(Path(sys.argv[0]).resolve())
    found = shutil.which("agentpool")
    return found or "agentpool"


def _provider_descriptor(manager: SessionManager, provider_id: str) -> dict[str, Any] | None:
    for provider in manager.inventory(include_usage=False)["providers"]:
        if provider["id"] == provider_id:
            return provider
    return None


def _usage_check(snapshot: dict[str, Any]) -> dict[str, Any]:
    status = snapshot.get("status", "unknown")
    warnings = snapshot.get("warnings") or []
    windows = snapshot.get("windows") or []
    ok = status not in {"unavailable", "unauthenticated", "unknown"}
    if status in {"near_limit", "limit_reached", "overage_possible"}:
        ok = False
    details = []
    for window in windows:
        name = window.get("name") or window.get("kind") or "window"
        remaining = window.get("remaining_percent")
        reset_at = window.get("reset_at")
        if remaining is not None:
            details.append(f"{name}: {remaining}% remaining")
        elif reset_at:
            details.append(f"{name}: resets {reset_at}")
    message = ", ".join(details) if details else f"status={status}"
    if warnings:
        message = f"{message}; warnings: {'; '.join(str(warning) for warning in warnings)}"
    return {
        "name": "usage_probe",
        "ok": ok,
        "message": message,
        "status": status,
        "confidence": snapshot.get("confidence"),
        "checked_at": snapshot.get("checked_at"),
        "action": None
        if ok
        else "Refresh usage after confirming the provider CLI is logged in, or rerun setup with `--skip-usage`.",
    }


def _setup_actions(checks: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for check in checks:
        if check.get("ok") is False and check.get("action"):
            actions.append(str(check["action"]))
    return actions


def _first_setup_action(result: dict[str, Any]) -> str | None:
    actions = result.get("actions") or _setup_actions(result.get("checks", []))
    if actions:
        return str(actions[0])
    setup_doc = result.get("setup_doc")
    if setup_doc:
        return f"See {setup_doc} for host-specific setup notes."
    return None


def _setup_host_next_commands(target: str) -> list[str]:
    return [
        "agentpool mcp-config --client cursor --absolute-command --install",
        "agentpool doctor --deep",
        "agentpool setup codex",
    ]


def _setup_next_commands(provider_id: str, target: str, setup: dict[str, Any] | None = None) -> list[str]:
    if setup and setup.get("kind") == "host":
        return _setup_host_next_commands(target)
    commands = [
        f"agentpool usage-summary --provider {provider_id} --refresh --json",
        f"agentpool models --provider {provider_id}",
    ]
    if target in {"codex", "codex-cli"}:
        commands.append("agentpool mcp-config --client codex --absolute-command --install")
    elif target in {"claude", "claude-code"}:
        commands.append("agentpool mcp-config --client claude-code --absolute-command --install")
    commands.append(
        f"agentpool spawn --provider {provider_id} --repo . --task \"Inspect this repo read-only.\" --isolation read_only"
    )
    return commands


def dumps_json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, default=str)


def _real_read_only_smoke_task() -> str:
    return """AgentPool real-provider read-only smoke test.

Rules:
- Do not create, modify, delete, move, or format files.
- Do not install packages.
- Do not access network services.
- Do not run expensive commands.
- Inspect the current directory only if needed.

Wait for a single steering message from AgentPool before doing anything.
"""


def _real_smoke_continue_message() -> str:
    return "Read-only smoke: do not edit files or run installs. Print the words AGENTPOOL, SMOKE, and DONE joined by underscores, then stop."


def _startup_trust_message(provider_id: str) -> str:
    if provider_id == "cursor-cli":
        return "a"
    return ""


def _smoke_model(manager: SessionManager, provider_id: str) -> str | None:
    config = manager.config.providers.get(provider_id)
    if not config:
        return None
    value = config.metadata.get("smoke_model") or config.metadata.get("default_model")
    return str(value) if value else None


def _is_startup_trust_prompt(text: str) -> bool:
    lowered = text.lower()
    return "do you trust" in lowered or "trust the files" in lowered or "trust this directory" in lowered


def _is_startup_update_prompt(text: str) -> bool:
    lowered = text.lower()
    return "update available" in lowered and ("skip" in lowered or "press enter to continue" in lowered)


def _is_startup_hook_prompt(text: str) -> bool:
    lowered = text.lower()
    return (
        "hooks need review" in lowered
        and "continue without trusting" in lowered
        and "trust all and continue" in lowered
    )


def _real_smoke_ok(output: dict[str, Any], final_observe: Any) -> bool:
    if output.get("blocked_reason") or output.get("git_dirty"):
        return False
    if final_observe and final_observe.event == ObserveEvent.ERROR:
        return False
    return bool(
        final_observe
        and final_observe.event == ObserveEvent.COMPLETED
        and output["lifecycle"].get("spawn")
        and output["lifecycle"].get("send")
        and output["lifecycle"].get("collect")
    )


def _safe_observe(
    manager: SessionManager,
    session_id: str,
    wait_for: list[str],
    timeout_seconds: int,
    output: dict[str, Any],
) -> Any | None:
    try:
        return manager.observe_worker(session_id, wait_for=wait_for, timeout_seconds=timeout_seconds)
    except ToolError as exc:
        output["observe_error"] = exc.error.model_dump(mode="json")
        return None


def _send_smoke_message(manager: SessionManager, session_id: str, provider_id: str) -> bool:
    return manager.send_worker_message(session_id, _real_smoke_continue_message())["ok"]


def _collect_smoke_artifacts(
    manager: SessionManager,
    session_id: str,
    output: dict[str, Any],
    final_observe: Any,
) -> None:
    try:
        collected = manager.collect_worker_artifacts(
            session_id,
            mark_completed=bool(final_observe and final_observe.state == SessionState.COMPLETED),
        )
    except ToolError as exc:
        output["collect_error"] = exc.error.model_dump(mode="json")
        return
    output["lifecycle"]["collect"] = True
    output["artifact_dir"] = collected["artifact_dir"]
    output["artifact_kinds"] = [artifact["kind"] for artifact in collected["artifacts"]]
    output["git_dirty"] = collected["git"]["dirty"]
