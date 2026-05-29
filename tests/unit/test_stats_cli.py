from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentpool.cli import app
from agentpool.config import AgentPoolConfig, StorageConfig
from agentpool.session_manager import SessionManager
from agentpool.store import Store


class RecordingRuntime:
    def spawn(self, command, cwd, env, session_name):
        from agentpool.models import TmuxSessionRef

        return TmuxSessionRef(session_name=session_name)

    def send_message(self, ref, text, submit=True):
        return None

    def send_keys(self, ref, keys):
        return None

    def capture(self, ref, lines=300):
        return ""

    def attach_command(self, ref):
        return f"tmux attach -t {ref.session_name}"

    def exists(self, ref):
        return True

    def terminate(self, ref):
        return None


@pytest.fixture
def stats_manager(tmp_path: Path) -> SessionManager:
    db_path = tmp_path / "agentpool.sqlite"
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(db_path),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    return SessionManager(
        config=config,
        store=Store(db_path),
        runtime=RecordingRuntime(),  # type: ignore[arg-type]
    )


def test_stats_json_on_empty_db_returns_schema_version(tmp_path: Path, stats_manager: SessionManager, monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: stats_manager)

    result = CliRunner().invoke(app, ["stats", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["schema_version"] == "stats/v1"
    assert data["scope"] == "all"


def test_stats_rich_on_empty_db_does_not_crash(stats_manager: SessionManager, monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: stats_manager)

    result = CliRunner().invoke(app, ["stats"])

    assert result.exit_code == 0
    assert "agentpool stats" in result.output.lower() or "sessions" in result.output.lower()


def test_stats_since_30d_json_honors_window(stats_manager: SessionManager, monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: stats_manager)

    result = CliRunner().invoke(app, ["stats", "--since", "30d", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["window"]["spec"] == "30d"
    assert data["window"]["label"] == "last 30d"


def test_stats_since_bogus_exits_with_invalid_window(stats_manager: SessionManager, monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: stats_manager)

    result = CliRunner().invoke(app, ["stats", "--since", "bogus", "--json"])

    assert result.exit_code == 1
    assert "INVALID_WINDOW" in result.output


def test_stats_plain_emits_key_value_lines_without_ansi(stats_manager: SessionManager, monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: stats_manager)

    result = CliRunner().invoke(app, ["stats", "--plain"])

    assert result.exit_code == 0
    assert "schema_version=" in result.output
    assert "scope=" in result.output
    assert "\x1b[" not in result.output


def test_stats_json_and_plain_are_mutually_exclusive(stats_manager: SessionManager, monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: stats_manager)

    result = CliRunner().invoke(app, ["stats", "--json", "--plain"])

    assert result.exit_code == 1
    assert "INVALID_OUTPUT" in result.output


def test_stats_share_writes_png_when_pillow_present(tmp_path: Path, stats_manager: SessionManager, monkeypatch) -> None:
    pytest.importorskip("PIL")
    monkeypatch.setattr("agentpool.cli.manager", lambda: stats_manager)
    card_path = tmp_path / "card.png"

    result = CliRunner().invoke(app, ["stats", "--share", str(card_path), "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert card_path.is_file()
    assert data["share_card"]["path"] == str(card_path)
    assert data["share_card"]["bytes"] > 0


def test_stats_share_missing_pillow_raises_missing_optional_dependency(
    tmp_path: Path,
    stats_manager: SessionManager,
    monkeypatch,
) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: stats_manager)
    real_import = builtins.__import__

    def mock_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "PIL" or (fromlist and "PIL" in name):
            raise ImportError("no pillow")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    result = CliRunner().invoke(app, ["stats", "--share", str(tmp_path / "card.png")])

    assert result.exit_code == 1
    assert "MISSING_OPTIONAL_DEPENDENCY" in result.output
