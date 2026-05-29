import json

from typer.testing import CliRunner

from agentpool import __version__
from agentpool.cli import _next_command_for_error, app
from agentpool.models import ToolError


def test_cli_version_option() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert f"agentpool {__version__}" in result.output


class Dumpable:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return self.payload


class CliManager:
    def __init__(self) -> None:
        self.spawn_task = None
        self.sent_message = None

    def spawn_worker(self, request):
        self.spawn_task = request.task
        return {
            "session": {"id": "ap_cli"},
            "attach_command": "tmux attach -t ap_cli",
        }

    def send_worker_message(self, session_id, message):
        self.sent_message = message
        return {"ok": True, "session_id": session_id}

    def send_worker_keys(self, session_id, keys):
        return {"ok": True, "session_id": session_id, "keys": keys}

    def interrupt_worker(self, session_id):
        return {"ok": True, "session_id": session_id, "state": "RUNNING"}

    def attach_info(self, session_id):
        return {
            "session_id": session_id,
            "attach_command": "tmux attach -t ap_cli",
            "tmux_session": "ap_cli",
            "pane_target": "ap_cli:0.0",
        }

    def terminate_worker(self, session_id):
        return {"ok": True, "session_id": session_id, "state": "CANCELLED"}

    def read_transcript(self, session_id, offset=0, limit=4000, tail_lines=None):
        return {
            "session_id": session_id,
            "path": "/tmp/artifacts/transcript.txt",
            "exists": True,
            "mode": "tail" if tail_lines else "page",
            "offset": None if tail_lines else offset,
            "limit": None if tail_lines else limit,
            "tail_lines": tail_lines,
            "next_offset": None if tail_lines else offset + 5,
            "has_more": False,
            "size_bytes": 5,
            "text": "hello",
        }

    def observe_worker(self, session_id, **kwargs):
        return Dumpable(
            {
                "session_id": session_id,
                "state": "RUNNING",
                "event": "none",
                "confidence": "unknown",
                "screen_excerpt": "worker text",
                "metadata": {"readiness": "running", "screen_hash": "abc"},
            }
        )

    def artifact_manifest(self, session_id):
        return {"session_id": session_id, "artifact_dir": "/tmp/artifacts", "files": []}

    def collect_worker_artifacts(self, session_id):
        return {
            "session_id": session_id,
            "state": "COMPLETED",
            "artifact_dir": "/tmp/artifacts",
            "artifacts": [],
            "summary": "worker result",
            "git": {},
        }

    def list_sessions(self, states=None, provider_id=None, limit=50, offset=0):
        return {
            "sessions": [{"id": "ap_cli", "provider_id": provider_id or "fake-question", "state": "RUNNING"}],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "count": 1,
                "total": 3,
                "has_more": True,
                "next_offset": offset + 1,
            },
            "scope": {"coordinator_id": "coord_cli", "current_coordinator_only": False},
            "states": states,
        }


def test_spawn_accepts_task_stdin(monkeypatch) -> None:
    manager = CliManager()
    monkeypatch.setattr("agentpool.cli.manager", lambda: manager)

    result = CliRunner().invoke(
        app,
        ["spawn", "--provider", "fake-question", "--repo", ".", "--task-stdin", "--json"],
        input="Inspect via stdin\n",
    )

    assert result.exit_code == 0
    assert manager.spawn_task == "Inspect via stdin"


def test_send_accepts_stdin(monkeypatch) -> None:
    manager = CliManager()
    monkeypatch.setattr("agentpool.cli.manager", lambda: manager)

    result = CliRunner().invoke(app, ["send", "ap_cli", "--stdin"], input="Continue\n")

    assert result.exit_code == 0
    assert manager.sent_message == "Continue"


def test_empty_stdin_errors_use_command_specific_examples() -> None:
    runner = CliRunner()

    spawn = runner.invoke(app, ["spawn", "--provider", "fake-question", "--repo", ".", "--task-stdin"], input="")
    send = runner.invoke(app, ["send", "ap_cli", "--stdin"], input="")

    assert spawn.exit_code == 1
    assert "cat task.md | agentpool spawn" in spawn.output
    assert send.exit_code == 1
    assert "cat reply.md | agentpool send" in send.output


def test_provider_selection_errors_point_to_inventory() -> None:
    for error in (
        ToolError("PROVIDER_NOT_FOUND", "missing", {"provider_id": "missing"}),
        ToolError("POLICY_BLOCKED", "auto disabled", {"provider_id": "auto", "policy": "require_explicit_provider"}),
        ToolError("POLICY_BLOCKED", "denied", {"provider_id": "codex-cli", "policy": "denied_providers"}),
    ):
        assert _next_command_for_error(error) == "agentpool inventory --json"


def test_control_commands_emit_json(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: CliManager())
    runner = CliRunner()

    cases = [
        ["send", "ap_cli", "Continue", "--json"],
        ["keys", "ap_cli", "Enter", "--json"],
        ["interrupt", "ap_cli", "--json"],
        ["attach", "ap_cli", "--json"],
        ["terminate", "ap_cli", "--json"],
    ]
    for args in cases:
        result = runner.invoke(app, args)
        assert result.exit_code == 0
        assert json.loads(result.output)


def test_transcript_reads_bounded_page(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: CliManager())

    json_result = CliRunner().invoke(app, ["transcript", "ap_cli", "--offset", "0", "--limit", "5", "--json"])
    text_result = CliRunner().invoke(app, ["transcript", "ap_cli", "--tail-lines", "1"])

    assert json_result.exit_code == 0
    assert json.loads(json_result.output)["text"] == "hello"
    assert text_result.exit_code == 0
    assert text_result.output == "hello"


def test_sessions_accepts_pagination_flags(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: CliManager())

    result = CliRunner().invoke(
        app,
        ["sessions", "--state", "running, awaiting_user_input", "--provider", "fake-question", "--limit", "1", "--offset", "2", "--json"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["pagination"]["limit"] == 1
    assert data["pagination"]["offset"] == 2
    assert data["states"] == ["running", "awaiting_user_input"]


def test_observe_summary_omits_worker_output(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: CliManager())

    result = CliRunner().invoke(app, ["observe", "ap_cli", "--json"])

    assert result.exit_code == 0
    assert '"worker_output"' in result.output
    assert '"included": false' in result.output
    assert "worker text" not in result.output


def test_collect_json_returns_manifest_shape(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: CliManager())

    result = CliRunner().invoke(app, ["collect", "ap_cli", "--json"])

    assert result.exit_code == 0
    assert '"artifact_dir": "/tmp/artifacts"' in result.output
    assert '"included": false' in result.output


def test_core_help_has_examples() -> None:
    runner = CliRunner()
    for command in (
        "usage-summary",
        "sessions",
        "spawn",
        "observe",
        "send",
        "keys",
        "interrupt",
        "attach",
        "collect",
        "artifacts",
        "transcript",
        "terminate",
        "mcp",
        "mcp-config",
    ):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0
        assert "Examples" in result.output
