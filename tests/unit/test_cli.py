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
        self.usage_calls = []
        self.terminated = None
        self.terminate_calls = 0
        self.cleaned_worktree = None

    def spawn_worker(self, request):
        self.spawn_task = request.task
        return {
            "session": {
                "id": "ap_cli",
                "provider_id": request.provider_id,
                "state": "RUNNING",
                "artifact_dir": "/tmp/artifacts",
                "worktree_path": None,
            },
            "attach_command": "tmux attach -t ap_cli",
            "artifact_dir": "/tmp/artifacts",
            "live_control": {"can_send": True},
            "preferences": {"path": "/tmp/preferences.md"},
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

    def terminate_worker(self, session_id, reason=None, dry_run=False):
        self.terminate_calls += 1
        self.terminated = {"session_id": session_id, "reason": reason, "dry_run": dry_run}
        return {
            "ok": True,
            "session_id": session_id,
            "state": "CANCELLED",
            "dry_run": dry_run,
            "already_terminated": self.terminate_calls > 1,
        }

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
            "sessions": [
                {
                    "id": "ap_cli",
                    "provider_id": provider_id or "fake-question",
                    "state": "RUNNING",
                    "role": "explorer",
                    "repo_path": "/repo",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
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

    def get_session(self, session_id):
        return {
            "session": {
                "id": session_id,
                "provider_id": "fake-question",
                "model": "fake",
                "state": "RUNNING",
                "role": "explorer",
                "repo_path": "/repo",
                "worktree_path": None,
                "created_at": "2026-01-01T00:00:00Z",
                "ended_at": None,
            }
        }

    def usage_snapshot(self, provider_id=None, backend="combined", allow_interactive=True):
        self.usage_calls.append(
            {"kind": "snapshot", "provider_id": provider_id, "backend": backend, "allow_interactive": allow_interactive}
        )
        return {"snapshots": [], "source": "live_probe", "backend": backend}

    def cached_usage_snapshot(self, provider_id=None):
        self.usage_calls.append({"kind": "cached", "provider_id": provider_id})
        return {"snapshots": [], "source": "sqlite_cache"}

    def usage_summary(self, provider_id=None, refresh=False, backend="combined", allow_interactive=True):
        self.usage_calls.append(
            {
                "kind": "summary",
                "provider_id": provider_id,
                "refresh": refresh,
                "backend": backend,
                "allow_interactive": allow_interactive,
            }
        )
        return {"providers": {}, "source": "live_probe" if refresh else "sqlite_cache", "backend": backend}

    def cleanup_worktree(self, session_id, force=False, dry_run=False):
        self.cleaned_worktree = {"session_id": session_id, "force": force, "dry_run": dry_run}
        return {"session_id": session_id, "removed": not dry_run, "would_remove": True, "dry_run": dry_run}


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


def test_spawn_human_output_is_structured(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: CliManager())

    result = CliRunner().invoke(
        app,
        ["spawn", "--provider", "fake-question", "--repo", ".", "--task", "Inspect read-only."],
    )

    assert result.exit_code == 0
    assert "session: ap_cli" in result.output
    assert "provider: fake-question" in result.output
    assert "{'session'" not in result.output


def test_preferences_command_inits_and_shows_markdown(tmp_path) -> None:
    path = tmp_path / "preferences.md"
    runner = CliRunner()

    init = runner.invoke(app, ["preferences", "init", "--path", str(path), "--json"])
    show = runner.invoke(app, ["preferences", "--path", str(path), "--json"])

    assert init.exit_code == 0
    assert json.loads(init.output)["changed"] is True
    assert show.exit_code == 0
    payload = json.loads(show.output)
    assert payload["path"] == str(path)
    assert payload["resource_uri"] == "agentpool://preferences.md"
    assert "AgentPool Preferences" in payload["text"]


def test_preferences_init_dry_run_has_no_side_effect(tmp_path) -> None:
    path = tmp_path / "preferences.md"

    result = CliRunner().invoke(app, ["preferences", "init", "--path", str(path), "--dry-run", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["would_write"] is True
    assert not path.exists()


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


def test_root_and_group_help_have_examples() -> None:
    runner = CliRunner()
    for args in (
        ["--help"],
        ["skills", "--help"],
        ["config", "--help"],
        ["leases", "--help"],
        ["session", "--help"],
        ["worktrees", "--help"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0
        assert "Examples:" in result.output


def test_root_help_points_agents_to_bundled_skills() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Start here (for AI agents):" in result.output
    assert "agentpool skills get agentpool" in result.output
    assert "skills [list]" in result.output
    assert "skills path [name]" in result.output
    assert "Skills ship with the CLI" in result.output


def test_skills_list_get_path_and_json() -> None:
    runner = CliRunner()

    listed = runner.invoke(app, ["skills"])
    listed_json = runner.invoke(app, ["skills", "list", "--json"])
    skill = runner.invoke(app, ["skills", "get", "agentpool"])
    full = runner.invoke(app, ["skills", "get", "core", "--full"])
    path = runner.invoke(app, ["skills", "path", "core"])
    skill_json = runner.invoke(app, ["skills", "get", "agentpool", "--json"])

    assert listed.exit_code == 0
    assert "agentpool" in listed.output
    assert "aliases: core" in listed.output
    assert listed_json.exit_code == 0
    assert json.loads(listed_json.output)["skills"][0]["name"] == "agentpool"
    assert skill.exit_code == 0
    assert "# AgentPool Skill" in skill.output
    assert "Typical CLI Flow" in skill.output
    assert full.exit_code == 0
    assert "# Reference: Quickstart" in full.output
    assert "# Examples" in full.output
    assert path.exit_code == 0
    assert "agentpool-skill.md" in path.output
    assert skill_json.exit_code == 0
    payload = json.loads(skill_json.output)
    assert payload["skills"][0]["name"] == "agentpool"
    assert "# AgentPool Skill" in payload["skills"][0]["text"]


def test_skills_get_unknown_returns_recovery_hint() -> None:
    result = CliRunner().invoke(app, ["skills", "get", "missing"])

    assert result.exit_code == 1
    assert "INVALID_REQUEST" in result.output
    assert "try: agentpool skills list" in result.output


def test_missing_parameter_errors_include_copy_pasteable_examples() -> None:
    runner = CliRunner()

    cases = {
        ("spawn",): "cat task.md | agentpool spawn --provider <provider-id>",
        ("setup",): "agentpool setup codex",
        ("observe",): "agentpool observe <session-id>",
        ("leases", "acquire"): "agentpool leases acquire --session-id <session-id> --file <path>",
        ("worktrees", "cleanup"): "agentpool worktrees cleanup --session-id <session-id> --dry-run --json",
    }
    for args, expected in cases.items():
        result = runner.invoke(app, list(args))
        assert result.exit_code in {1, 2}
        if result.output:
            assert f"try: {expected}" in result.output
        else:
            assert result.exception.__class__.__name__ == "MissingParameter"


def test_models_bad_action_uses_recovery_formatter() -> None:
    result = CliRunner().invoke(app, ["models", "foo"])

    assert result.exit_code == 1
    assert "INVALID_REQUEST" in result.output
    assert "try: agentpool models validate --json" in result.output


def test_invalid_output_errors_include_next_command() -> None:
    runner = CliRunner()
    for args in (["stats", "--json", "--plain"], ["sessions", "--json", "--plain"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["error"]["details"]["example"] == "agentpool stats --since 7d --json"


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


def test_terminate_dry_run_passes_through(monkeypatch) -> None:
    manager = CliManager()
    monkeypatch.setattr("agentpool.cli.manager", lambda: manager)

    result = CliRunner().invoke(app, ["terminate", "ap_cli", "--dry-run", "--json"])

    assert result.exit_code == 0
    assert manager.terminated == {"session_id": "ap_cli", "reason": None, "dry_run": True}
    assert json.loads(result.output)["dry_run"] is True


def test_terminate_json_preserves_already_terminated(monkeypatch) -> None:
    manager = CliManager()
    monkeypatch.setattr("agentpool.cli.manager", lambda: manager)
    runner = CliRunner()

    first = runner.invoke(app, ["terminate", "ap_cli", "--json"])
    second = runner.invoke(app, ["terminate", "ap_cli", "--json"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert json.loads(first.output)["already_terminated"] is False
    assert json.loads(second.output)["already_terminated"] is True


def test_lifecycle_human_outputs_are_not_raw_dicts(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: CliManager())
    runner = CliRunner()

    for args, expected in (
        (["interrupt", "ap_cli"], "session: ap_cli"),
        (["terminate", "ap_cli"], "already_terminated: no"),
        (["collect", "ap_cli"], "artifact_dir: /tmp/artifacts"),
        (["artifacts", "ap_cli"], "files: none"),
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0
        assert expected in result.output
        assert "{'" not in result.output


def test_interrupt_dry_run_does_not_call_manager(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: (_ for _ in ()).throw(AssertionError("manager called")))

    result = CliRunner().invoke(app, ["interrupt", "ap_cli", "--dry-run", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["would_interrupt"] is True


def test_leases_release_dry_run_has_no_side_effect(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: (_ for _ in ()).throw(AssertionError("manager called")))

    result = CliRunner().invoke(app, ["leases", "release", "--lease-id", "7", "--dry-run", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["lease_id"] == 7


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


def test_sessions_human_and_plain_outputs_are_not_raw_dicts(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: CliManager())
    runner = CliRunner()

    human = runner.invoke(app, ["sessions"])
    plain = runner.invoke(app, ["sessions", "--plain"])

    assert human.exit_code == 0
    assert "ap_cli" in human.output
    assert "{'sessions'" not in human.output
    assert plain.exit_code == 0
    assert "sessions.0.id=ap_cli" in plain.output


def test_session_show_returns_one_session(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: CliManager())

    result = CliRunner().invoke(app, ["session", "show", "ap_cli", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["session"]["id"] == "ap_cli"


def test_session_show_plain_returns_key_value_lines(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: CliManager())

    result = CliRunner().invoke(app, ["session", "show", "ap_cli", "--plain"])

    assert result.exit_code == 0
    assert "session.id=ap_cli" in result.output
    assert "session.provider_id=fake-question" in result.output
    assert "{'session'" not in result.output


def test_usage_no_interactive_flag_and_env_pass_through(monkeypatch) -> None:
    manager = CliManager()
    monkeypatch.setattr("agentpool.cli.manager", lambda: manager)

    result = CliRunner().invoke(app, ["usage", "--provider", "claude-code", "--no-interactive", "--json"])

    assert result.exit_code == 0
    assert manager.usage_calls[-1]["allow_interactive"] is False

    result = CliRunner().invoke(app, ["usage", "--provider", "claude-code", "--no-interactive-usage", "--json"])

    assert result.exit_code == 0
    assert manager.usage_calls[-1]["allow_interactive"] is False

    monkeypatch.setenv("AGENTPOOL_NO_INTERACTIVE_USAGE", "1")
    result = CliRunner().invoke(app, ["usage-summary", "--provider", "claude-code", "--refresh", "--json"])

    assert result.exit_code == 0
    assert manager.usage_calls[-1]["kind"] == "summary"
    assert manager.usage_calls[-1]["allow_interactive"] is False


def test_worktrees_cleanup_dry_run_and_yes_alias_pass_through(monkeypatch) -> None:
    manager = CliManager()
    monkeypatch.setattr("agentpool.cli.manager", lambda: manager)

    result = CliRunner().invoke(
        app,
        ["worktrees", "cleanup", "--session-id", "ap_cli", "--dry-run", "--yes", "--json"],
    )

    assert result.exit_code == 0
    assert manager.cleaned_worktree == {"session_id": "ap_cli", "force": True, "dry_run": True}


def test_config_path_and_print_accept_json() -> None:
    runner = CliRunner()

    path = runner.invoke(app, ["config", "path", "--json"])
    printed = runner.invoke(app, ["config", "print", "--json"])

    assert path.exit_code == 0
    assert "config.yaml" in json.loads(path.output)["path"]
    assert printed.exit_code == 0
    assert "providers" in json.loads(printed.output)


def test_observe_summary_omits_worker_output(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: CliManager())

    result = CliRunner().invoke(app, ["observe", "ap_cli", "--json"])

    assert result.exit_code == 0
    assert '"worker_output"' in result.output
    assert '"included": false' in result.output
    assert "worker text" not in result.output


def test_observe_output_writes_json_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: CliManager())
    output = tmp_path / "observe.json"

    result = CliRunner().invoke(app, ["observe", "ap_cli", "--detail", "excerpt", "--output", str(output)])

    assert result.exit_code == 0
    assert "observe.json" in result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["session_id"] == "ap_cli"
    assert payload["worker_output"]["included"] is True


def test_collect_json_returns_manifest_shape(monkeypatch) -> None:
    monkeypatch.setattr("agentpool.cli.manager", lambda: CliManager())

    result = CliRunner().invoke(app, ["collect", "ap_cli", "--json"])

    assert result.exit_code == 0
    assert '"artifact_dir": "/tmp/artifacts"' in result.output
    assert '"included": false' in result.output


def test_core_help_has_examples() -> None:
    runner = CliRunner()
    for command in (
        "doctor",
        "init",
        "inventory",
        "usage",
        "usage-summary",
        "capacity-summary",
        "setup",
        "onboard",
        "preferences",
        "smoke",
        "providers",
        "skills",
        "models",
        "stats",
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


def test_nested_help_has_examples() -> None:
    runner = CliRunner()
    for command in (
        ("session", "show"),
        ("skills", "list"),
        ("skills", "get"),
        ("skills", "path"),
        ("config", "path"),
        ("config", "print"),
        ("config", "validate"),
        ("leases", "list"),
        ("leases", "acquire"),
        ("leases", "release"),
        ("worktrees", "list"),
        ("worktrees", "cleanup"),
    ):
        result = runner.invoke(app, [*command, "--help"])
        assert result.exit_code == 0
        assert "Examples" in result.output
