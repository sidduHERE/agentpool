from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentpool.config import AgentPoolConfig, StorageConfig
from agentpool.onboarding import (
    deep_doctor,
    format_mcp_install,
    init_config,
    mcp_client_config,
    mcp_host_config,
    setup_all_providers,
    setup_provider,
)
from agentpool.session_manager import SessionManager
from agentpool.store import Store


class FakeSetupManager:
    def inventory(self, include_usage: bool = True) -> dict[str, Any]:
        return {
            "providers": [
                {
                    "id": "codex-cli",
                    "display_name": "Codex CLI",
                    "installed": True,
                    "binary_path": "/usr/local/bin/codex",
                    "version": "codex 1.0.0",
                    "auth": {
                        "status": "unknown",
                        "confidence": "unknown",
                        "reason": "No safe auth probe implemented.",
                    },
                    "metadata": {},
                },
                {
                    "id": "claude-code",
                    "display_name": "Claude Code",
                    "installed": False,
                    "binary_path": None,
                    "version": None,
                    "auth": {"status": "unavailable", "confidence": "unknown", "reason": "missing"},
                    "metadata": {},
                },
                {
                    "id": "copilot-cli",
                    "display_name": "GitHub Copilot CLI",
                    "installed": False,
                    "binary_path": None,
                    "version": None,
                    "auth": {"status": "unavailable", "confidence": "unknown", "reason": "missing"},
                    "metadata": {},
                },
                {
                    "id": "devin-cli",
                    "display_name": "Devin CLI",
                    "installed": False,
                    "binary_path": None,
                    "version": None,
                    "auth": {"status": "unavailable", "confidence": "unknown", "reason": "missing"},
                    "metadata": {},
                },
                {
                    "id": "cursor-cli",
                    "display_name": "Cursor Agent CLI",
                    "installed": False,
                    "binary_path": None,
                    "version": None,
                    "auth": {"status": "unavailable", "confidence": "unknown", "reason": "missing"},
                    "metadata": {},
                },
                {
                    "id": "droid-cli",
                    "display_name": "Droid CLI",
                    "installed": False,
                    "binary_path": None,
                    "version": None,
                    "auth": {"status": "unavailable", "confidence": "unknown", "reason": "missing"},
                    "metadata": {},
                },
            ]
        }

    def provider_models(self, provider_id: str | None = None) -> dict[str, Any]:
        default_model = None
        models = []
        if provider_id == "codex-cli":
            default_model = "gpt-5.5"
            models = [{"id": "gpt-5.5"}]
        elif provider_id == "cursor-cli":
            default_model = "composer-2.5"
            models = [{"id": "composer-2.5"}]
        return {
            "providers": [
                {
                    "provider_id": provider_id,
                    "default_model": default_model,
                    "models": models,
                }
            ]
        }

    def usage_snapshot(
        self,
        provider_id: str | None = None,
        backend: str = "combined",
        allow_interactive: bool = True,
    ) -> dict[str, Any]:
        assert allow_interactive is True
        assert backend == ("codexbar" if provider_id == "cursor-cli" else "native")
        if provider_id != "codex-cli":
            return {
                "snapshots": [
                    {
                        "provider_id": provider_id,
                        "status": "unavailable",
                        "confidence": "unknown",
                        "checked_at": "2026-05-19T00:00:00Z",
                        "windows": [],
                        "warnings": ["Provider binary is not installed."],
                    }
                ],
                "source": "live_probe",
                "backend": backend,
            }
        return {
            "snapshots": [
                {
                    "provider_id": "codex-cli",
                    "status": "available",
                    "confidence": "official",
                    "checked_at": "2026-05-19T00:00:00Z",
                    "windows": [{"name": "5h", "remaining_percent": 80}],
                }
            ],
            "source": "live_probe",
            "backend": backend,
        }

    def usage_summary(
        self,
        provider_id: str | None = None,
        refresh: bool = False,
        backend: str = "combined",
        allow_interactive: bool = True,
    ) -> dict[str, Any]:
        assert refresh is False
        assert allow_interactive is True
        return {
            "providers": {
                provider_id: {
                    "provider_id": provider_id,
                    "status": "available",
                    "usable": provider_id == "codex-cli",
                }
            },
            "source": "sqlite_cache",
        }


def test_init_config_is_idempotent_and_backs_up_on_force(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"

    first = init_config(path)
    second = init_config(path)
    forced = init_config(path, force=True)

    assert first["changed"] is True
    assert second["changed"] is False
    assert forced["changed"] is True
    assert forced["backup_path"]
    assert Path(forced["backup_path"]).exists()
    assert first["preferences"]["changed"] is True
    assert second["preferences"]["changed"] is False
    assert Path(first["preferences"]["path"]).name == "preferences.md"
    assert Path(first["preferences"]["path"]).exists()
    assert "agentpool setup cursor" in first["next_commands"]


def test_mcp_host_config_shape() -> None:
    config = mcp_host_config("/tmp/agentpool")

    assert config == {"mcpServers": {"agentpool": {"command": "/tmp/agentpool", "args": ["mcp"]}}}


def test_mcp_client_config_codex_toml() -> None:
    config = mcp_client_config("codex", "/tmp/agentpool")

    assert config["ok"] is True
    assert config["format"] == "toml"
    assert "[mcp_servers.agentpool]" in config["config"]
    assert 'command = "/tmp/agentpool"' in config["config"]
    assert 'args = ["mcp"]' in config["config"]
    assert config["commands"] == ["codex mcp add agentpool -- /tmp/agentpool mcp"]
    assert config["install"]["kind"] == "shell"


def test_mcp_client_config_claude_code_command() -> None:
    config = mcp_client_config("claude-code", "/tmp/agentpool")

    assert config["ok"] is True
    assert config["config"] == {
        "mcpServers": {
            "agentpool": {"type": "stdio", "command": "/tmp/agentpool", "args": ["mcp"], "env": {}}
        }
    }
    assert (
        config["commands"][0]
        == "claude mcp add --transport stdio --scope local agentpool -- /tmp/agentpool mcp"
    )
    assert config["install"]["kind"] == "shell"


def test_mcp_client_config_cursor_install_deeplink() -> None:
    config = mcp_client_config("cursor", "/tmp/agentpool")

    assert config["ok"] is True
    assert config["config"]["mcpServers"]["agentpool"]["type"] == "stdio"
    assert config["install"]["kind"] == "deeplink"
    deeplink = config["install"]["deeplink"]
    assert deeplink.startswith("cursor://anysphere.cursor-deeplink/mcp/install?")
    assert "name=agentpool" in deeplink
    assert "config=" in deeplink


def test_mcp_client_config_copilot_cli_local_server() -> None:
    config = mcp_client_config("copilot-cli", "/tmp/agentpool")

    assert config["ok"] is True
    assert config["path"] == "~/.copilot/mcp-config.json"
    assert config["config"]["mcpServers"]["agentpool"] == {
        "type": "local",
        "command": "/tmp/agentpool",
        "args": ["mcp"],
        "tools": ["*"],
    }
    assert config["commands"] == ["copilot mcp add agentpool -- /tmp/agentpool mcp"]
    assert config["install"]["kind"] == "shell"


def test_format_mcp_install_renders_shell_and_deeplink() -> None:
    config = mcp_client_config("codex", "/tmp/agentpool")
    text = format_mcp_install(config)

    assert "codex mcp add agentpool -- /tmp/agentpool mcp" in text
    assert "Verify with `codex mcp list`" in text

    cursor = mcp_client_config("cursor", "/tmp/agentpool")
    cursor_text = format_mcp_install(cursor)
    assert cursor_text.startswith("MCP install helper for cursor:")
    assert "cursor://anysphere.cursor-deeplink/mcp/install?" in cursor_text


def test_mcp_client_config_rejects_unknown_client() -> None:
    config = mcp_client_config("mystery")

    assert config["ok"] is False
    assert "codex" in config["supported_clients"]


def test_mcp_client_config_rejects_vscode_aliases() -> None:
    for client in ("vscode", "copilot-vscode"):
        config = mcp_client_config(client)
        assert config["ok"] is False
        assert client not in config["supported_clients"]


def test_setup_cursor_host_reports_install_helper() -> None:
    result = setup_provider(FakeSetupManager(), "cursor", absolute_command=False)

    assert result["ok"] is True
    assert result["kind"] == "host"
    assert result["provider_id"] is None
    assert result["mcp_config"]["client"] == "cursor"
    assert result["mcp_config"]["install"]["kind"] == "deeplink"
    assert {check["name"] for check in result["checks"]} == {"agentpool_installed", "mcp_config"}
    assert "agentpool mcp-config --client cursor --absolute-command --install" in result["next_commands"]


def test_setup_provider_droid_cli_includes_setup_doc() -> None:
    result = setup_provider(FakeSetupManager(), "droid-cli", run_usage=False, absolute_command=False)

    assert result["provider_id"] == "droid-cli"
    assert result["setup_doc"] == "docs/setup-droid.md"
    installed = next(check for check in result["checks"] if check["name"] == "provider_installed")
    assert installed["ok"] is False


def test_setup_provider_cursor_cli_uses_codexbar_usage_backend() -> None:
    result = setup_provider(FakeSetupManager(), "cursor-cli", absolute_command=False)

    assert result["provider_id"] == "cursor-cli"
    assert result["setup_doc"] == "docs/setup-cursor-cli.md"
    assert result["usage"]["backend"] == "codexbar"
    installed = next(check for check in result["checks"] if check["name"] == "provider_installed")
    assert installed["ok"] is False


def test_server_json_matches_package_version() -> None:
    payload = json.loads(Path("server.json").read_text(encoding="utf-8"))
    from agentpool import __version__

    assert payload["version"] == __version__
    # Now that agentpool-cli is on PyPI, server.json advertises the package.
    packages = payload["packages"]
    assert len(packages) == 1
    pkg = packages[0]
    assert pkg["registryType"] == "pypi"
    assert pkg["identifier"] == "agentpool-cli"
    assert pkg["version"] == __version__
    assert pkg["transport"]["type"] == "stdio"
    assert pkg["packageArguments"] == [{"type": "positional", "value": "mcp"}]


def test_setup_provider_codex_reports_checks_and_mcp_config() -> None:
    result = setup_provider(FakeSetupManager(), "codex", absolute_command=False)

    assert result["ok"] is True
    assert result["provider_id"] == "codex-cli"
    assert {check["name"] for check in result["checks"]} >= {
        "provider_configured",
        "provider_installed",
        "model_catalog",
        "usage_probe",
        "mcp_config",
    }
    assert result["mcp_config"]["format"] == "toml"
    assert "[mcp_servers.agentpool]" in result["mcp_config"]["config"]
    assert "agentpool mcp-config --client codex --absolute-command --install" in result["next_commands"]


def test_setup_provider_can_skip_live_usage() -> None:
    result = setup_provider(FakeSetupManager(), "codex", run_usage=False, absolute_command=False)

    usage_check = next(check for check in result["checks"] if check["name"] == "usage_probe")
    assert result["ok"] is True
    assert usage_check["ok"] is None
    assert "Skipped" in usage_check["message"]
    assert "usage-summary" in usage_check["action"]


def test_setup_provider_rejects_unknown_target() -> None:
    result = setup_provider(FakeSetupManager(), "mystery")

    assert result["ok"] is False
    assert "codex" in result["supported_targets"]
    assert "agentpool setup all" in result["action"]


def test_setup_provider_missing_binary_includes_action() -> None:
    result = setup_provider(FakeSetupManager(), "claude-code", run_usage=False, absolute_command=False)

    installed = next(check for check in result["checks"] if check["name"] == "provider_installed")
    assert result["ok"] is False
    assert installed["ok"] is False
    assert "claude --version" in installed["action"]
    assert installed["action"] in result["actions"]


def test_setup_all_providers_returns_summary_rows() -> None:
    result = setup_all_providers(FakeSetupManager(), absolute_command=False)

    assert result["ok"] is False
    assert [row["provider_id"] for row in result["rows"]] == [
        "codex-cli",
        "claude-code",
        "copilot-cli",
        "devin-cli",
        "droid-cli",
        "cursor-cli",
    ]
    codex = result["rows"][0]
    assert codex["ok"] is True
    assert codex["mcp_config"] == "yes"
    assert codex["action"] == "See docs/setup-codex.md for host-specific setup notes."
    assert result["rows"][1]["installed"] is False
    assert "claude --version" in result["rows"][1]["action"]


def test_deep_doctor_basic_checks(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    manager = SessionManager(config=config, store=Store(tmp_path / "agentpool.sqlite"))

    result = deep_doctor(manager)

    assert {check["name"] for check in result["checks"]} == {
        "tmux_roundtrip",
        "terminal_control",
        "sqlite",
        "artifact_root",
        "usage_cache",
        "codexbar",
    }
    assert "agentpool setup cursor" in result["next_commands"]
    assert all(isinstance(check["ok"], bool) for check in result["checks"])
