from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from agentpool.config import (
    AgentPoolConfig,
    FAKE_AGENT_DIR,
    StorageConfig,
    default_provider_config,
    load_config,
    load_model_catalog,
    validate_model_catalog_path,
)
from agentpool.models import (
    AgentSession,
    CapacitySnapshot,
    Confidence,
    ObserveEvent,
    RuntimeKind,
    SessionState,
    SpawnWorkerRequest,
    ToolError,
    TmuxSessionRef,
    UsageStatus,
    UsageWindow,
    UsageWindowKind,
    now_utc,
)
from agentpool.providers.registry import build_registry
from agentpool.session_manager import SessionManager
from agentpool.store import Store


class RecordingRuntime:
    def __init__(self) -> None:
        self.command: list[str] | None = None
        self.sent_messages: list[str] = []
        self.sent_keys: list[list[str]] = []
        self.terminated = False
        self.exists_result = True
        self.screen = ""

    def spawn(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        session_name: str,
    ) -> TmuxSessionRef:
        self.command = command
        return TmuxSessionRef(session_name=session_name)

    def send_message(self, ref: TmuxSessionRef, text: str, submit: bool = True) -> None:
        self.sent_messages.append(text)

    def send_keys(self, ref: TmuxSessionRef, keys: list[str]) -> None:
        self.sent_keys.append(keys)

    def capture(self, ref: TmuxSessionRef, lines: int = 300) -> str:
        return self.screen

    def attach_command(self, ref: TmuxSessionRef) -> str:
        return f"tmux attach -t {ref.session_name}"

    def exists(self, ref: TmuxSessionRef) -> bool:
        return self.exists_result

    def terminate(self, ref: TmuxSessionRef) -> None:
        self.terminated = True


class FailingRuntime(RecordingRuntime):
    def spawn(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        session_name: str,
    ) -> TmuxSessionRef:
        raise RuntimeError("boom")


def test_config_defaults_include_fake_and_real_providers() -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    assert config.policy.require_explicit_provider is True
    assert config.policy.allow_auto_routing is False
    assert "fake-question" in config.providers
    assert "codex-cli" in config.providers


def test_default_fake_provider_commands_are_packaged() -> None:
    providers = default_provider_config()

    for provider_id, provider in providers.items():
        if not provider_id.startswith("fake-"):
            continue
        assert provider.command is not None
        script_path = Path(provider.command[-1])
        assert script_path.is_file()
        assert script_path.is_relative_to(FAKE_AGENT_DIR)


def test_spawn_request_rejects_auto_provider_in_policy_layer() -> None:
    request = SpawnWorkerRequest(provider_id="codex-cli", task="inspect", repo_path=".")
    assert request.runtime == RuntimeKind.TMUX
    assert request.isolation == "read_only"


def test_store_round_trips_session(tmp_path: Path) -> None:
    store = Store(tmp_path / "agentpool.sqlite")
    session = AgentSession(
        id="ap_test",
        provider_id="fake-question",
        harness="fake-question",
        role="explorer",
        task="inspect",
        repo_path=str(tmp_path),
        runtime=RuntimeKind.TMUX,
        state=SessionState.RUNNING,
        created_at=now_utc(),
        updated_at=now_utc(),
        artifact_dir=str(tmp_path / "artifacts"),
        transcript_path=str(tmp_path / "artifacts" / "transcript.txt"),
        events_path=str(tmp_path / "artifacts" / "events.jsonl"),
    )
    store.save_session(session)
    store.append_event(session.id, "spawn", state=SessionState.RUNNING.value)
    loaded = store.get_session(session.id)
    assert loaded is not None
    assert loaded.provider_id == "fake-question"
    assert store.list_events(session.id)[0]["event_type"] == "spawn"


def test_store_file_lease_conflict_and_release(tmp_path: Path) -> None:
    store = Store(tmp_path / "agentpool.sqlite")
    first = store.acquire_file_lease("s1", str(tmp_path), "src/app.py", mode="write")

    with pytest.raises(ToolError) as exc:
        store.acquire_file_lease("s2", str(tmp_path), "src/app.py", mode="write")
    assert exc.value.error.code == "LEASE_CONFLICT"
    assert len(store.list_file_leases(active_only=True)) == 1
    assert store.release_file_lease(lease_id=first.id) == 1
    assert store.list_file_leases(active_only=True) == []


def test_store_round_trips_latest_usage_snapshot(tmp_path: Path) -> None:
    store = Store(tmp_path / "agentpool.sqlite")
    older = CapacitySnapshot(
        provider_id="codex-cli",
        status=UsageStatus.NEAR_LIMIT,
        confidence=Confidence.OFFICIAL,
        windows=[
            UsageWindow(
                name="5h",
                kind=UsageWindowKind.FIVE_HOUR,
                remaining_percent=12,
                used_percent=88,
                confidence=Confidence.OFFICIAL,
            )
        ],
    )
    newer = CapacitySnapshot(
        provider_id="codex-cli",
        status=UsageStatus.AVAILABLE,
        confidence=Confidence.OFFICIAL,
        windows=[
            UsageWindow(
                name="5h",
                kind=UsageWindowKind.FIVE_HOUR,
                remaining_percent=60,
                used_percent=40,
                confidence=Confidence.OFFICIAL,
            )
        ],
    )
    store.save_usage_snapshot(older)
    store.save_usage_snapshot(newer)

    snapshots = store.latest_usage_snapshots("codex-cli")

    assert len(snapshots) == 1
    assert snapshots[0].provider_id == "codex-cli"
    assert snapshots[0].status == UsageStatus.AVAILABLE
    assert snapshots[0].windows[0].kind == UsageWindowKind.FIVE_HOUR
    assert snapshots[0].windows[0].remaining_percent == 60


def test_custom_config_merges(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
version: 1
storage:
  db_path: ./custom.sqlite
policy:
  max_parallel_sessions: 2
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert isinstance(config, AgentPoolConfig)
    assert isinstance(config.storage, StorageConfig)
    assert config.storage.db_path == "./custom.sqlite"
    assert config.policy.max_parallel_sessions == 2
    assert "fake-question" in config.providers


def test_default_model_catalog_applies_provider_defaults() -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))

    assert config.providers["codex-cli"].metadata["default_model"] == "gpt-5.5"
    assert config.providers["codex-cli"].metadata["submit_keys"] == ["C-m"]
    assert config.providers["codex-cli"].metadata["default_initial_prompt_mode"] == "arg"
    assert config.providers["codex-cli"].metadata["reasoning_effort_config_key"] == "model_reasoning_effort"
    assert config.providers["codex-cli"].metadata["service_tier_config_key"] == "service_tier"
    assert config.providers["cursor-cli"].metadata["default_model"] == "composer-2.5"
    assert config.providers["cursor-cli"].metadata["default_initial_prompt_mode"] == "arg"
    assert config.providers["cursor-cli"].metadata["read_only_mode_arg"] == "ask"
    assert config.providers["droid-cli"].metadata["model_selection"] == "runtime_settings"
    assert "factory-droid" not in config.providers
    assert {model["id"] for model in config.providers["droid-cli"].models} >= {"glm-5.1", "gpt-5.5"}


def test_load_config_drops_deprecated_gemini_cli_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "agentpool.yaml"
    config_path.write_text(
        """
version: 1
providers:
  gemini-cli:
    enabled: true
    binary_candidates: [gemini]
    models:
      - id: gemini-3-flash-preview
        source: config
        confidence: observed
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert "gemini-cli" not in config.providers


def test_stale_packaged_fake_provider_paths_are_repaired(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    stale_script = tmp_path / "old-venv" / "agentpool" / "fixtures" / "fake_agents" / "fake_question_agent.py"
    config_path.write_text(
        f"""
providers:
  fake-question:
    binary_candidates:
      - {tmp_path / "old-venv" / "bin" / "python"}
    command:
      - {tmp_path / "old-venv" / "bin" / "python"}
      - {stale_script}
    metadata:
      fake: true
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    command = config.providers["fake-question"].command
    assert command is not None
    assert command[0] == sys.executable
    assert Path(command[1]).exists()


def test_model_catalog_matches_golden_fixture() -> None:
    golden_path = Path("tests/fixtures/provider_model_catalog_golden.json")
    expected = json.loads(golden_path.read_text(encoding="utf-8"))

    assert _catalog_summary(load_model_catalog()) == expected


def test_load_config_refreshes_stale_embedded_provider_models(tmp_path: Path) -> None:
    config_path = tmp_path / "agentpool.yaml"
    config_path.write_text(
        """
version: 1
providers:
  codex-cli:
    models:
      - id: gpt-5.4
        source: config
        confidence: observed
        metadata:
          reasoning:
            supported: [low, medium, high, xhigh]
            default: medium
""",
        encoding="utf-8",
    )

    config = load_config(config_path)
    codex = config.providers["codex-cli"]
    gpt54 = next(model for model in codex.models if model["id"] == "gpt-5.4")

    assert gpt54["metadata"]["reasoning"]["default"] == "high"


def test_user_model_catalog_path_overrides_defaults(tmp_path: Path) -> None:
    model_catalog = tmp_path / "models.json"
    model_catalog.write_text(
        """
{
  "version": 1,
  "providers": {
    "codex-cli": {
      "default_model": "gpt-5.3-codex",
      "smoke_model": "gpt-5.3-codex",
      "models": [
        {
          "id": "gpt-5.3-codex",
          "source": "config",
          "confidence": "user_configured"
        }
      ]
    }
  }
}
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
version: 1
model_catalog_paths:
  - {model_catalog}
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.providers["codex-cli"].metadata["default_model"] == "gpt-5.3-codex"
    assert config.providers["codex-cli"].metadata["smoke_model"] == "gpt-5.3-codex"
    assert [model["id"] for model in config.providers["codex-cli"].models] == ["gpt-5.3-codex"]


def test_direct_provider_metadata_override_wins_after_catalog(tmp_path: Path) -> None:
    model_catalog = tmp_path / "models.json"
    model_catalog.write_text(
        """
{
  "version": 1,
  "providers": {
    "codex-cli": {
      "default_model": "gpt-5.3-codex",
      "smoke_model": "gpt-5.3-codex"
    }
  }
}
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
version: 1
model_catalog_paths:
  - {model_catalog}
providers:
  codex-cli:
    metadata:
      default_model: gpt-5.5
      smoke_model: gpt-5.5
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.providers["codex-cli"].metadata["default_model"] == "gpt-5.5"
    assert config.providers["codex-cli"].metadata["smoke_model"] == "gpt-5.5"


def test_model_catalog_paths_are_json_only(tmp_path: Path) -> None:
    model_catalog = tmp_path / "models.yaml"
    model_catalog.write_text("version: 1\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
version: 1
model_catalog_paths:
  - {model_catalog}
""",
        encoding="utf-8",
    )

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "must be JSON files" in str(exc)
    else:
        raise AssertionError("YAML model catalog path should be rejected")


def test_model_catalog_validation_rejects_ambiguous_reasoning_values(tmp_path: Path) -> None:
    model_catalog = tmp_path / "models.json"
    model_catalog.write_text(
        """
{
  "version": 1,
  "providers": {
    "droid-cli": {
      "models": [
        {
          "id": "glm-5.1",
          "source": "config",
          "confidence": "observed",
          "metadata": {
            "reasoning": {
              "supported": [false, "high"],
              "default": false
            }
          }
        }
      ]
    }
  }
}
""",
        encoding="utf-8",
    )

    result = validate_model_catalog_path(model_catalog, known_provider_ids={"droid-cli"})

    assert result["ok"] is False
    assert any("supported values must be strings" in error for error in result["errors"])
    assert any("default must be a string" in error for error in result["errors"])


def test_spawn_uses_provider_default_model_when_model_is_omitted(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.providers["codex-cli"].command = [sys.executable]
    runtime = RecordingRuntime()
    manager = SessionManager(config=config, runtime=runtime)  # type: ignore[arg-type]

    result = manager.spawn_worker(
        SpawnWorkerRequest(provider_id="codex-cli", task="inspect", repo_path=str(tmp_path))
    )

    assert result["session"]["model"] == "gpt-5.5"
    assert result["session"]["metadata"]["initial_prompt_mode"] == "arg"
    assert result["live_control"]["initial_prompt_mode"] == "arg"
    assert runtime.command is not None
    assert runtime.command[-3:-1] == ["--model", "gpt-5.5"]
    assert runtime.command[-1].startswith("You are running as a delegated worker session under AgentPool.")
    assert runtime.sent_messages == []


def test_spawn_codex_honors_reasoning_and_service_tier(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.providers["codex-cli"].command = [sys.executable]
    runtime = RecordingRuntime()
    manager = SessionManager(config=config, runtime=runtime)  # type: ignore[arg-type]

    manager.spawn_worker(
        SpawnWorkerRequest(
            provider_id="codex-cli",
            task="inspect",
            repo_path=str(tmp_path),
            model="gpt-5.4",
            reasoning_effort="high",
            service_tier="fast",
            initial_prompt_mode="arg",
        )
    )

    assert runtime.command is not None
    assert runtime.command[:-1] == [
        sys.executable,
        "--model",
        "gpt-5.4",
        "-c",
        'model_reasoning_effort="high"',
        "-c",
        'service_tier="fast"',
    ]


def test_spawn_codex_uses_catalog_reasoning_for_explicit_model(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.providers["codex-cli"].command = [sys.executable]
    runtime = RecordingRuntime()
    manager = SessionManager(config=config, runtime=runtime)  # type: ignore[arg-type]

    result = manager.spawn_worker(
        SpawnWorkerRequest(
            provider_id="codex-cli",
            task="inspect",
            repo_path=str(tmp_path),
            model="gpt-5.4",
            initial_prompt_mode="arg",
        )
    )

    assert runtime.command is not None
    assert runtime.command[:-1] == [
        sys.executable,
        "--model",
        "gpt-5.4",
        "-c",
        'model_reasoning_effort="high"',
    ]
    assert result["session"]["metadata"]["reasoning_effort"] == "high"


def test_spawn_initial_prompt_arg_appends_prompt_without_logging_body(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.providers["codex-cli"].command = [sys.executable]
    runtime = RecordingRuntime()
    manager = SessionManager(config=config, runtime=runtime)  # type: ignore[arg-type]

    result = manager.spawn_worker(
        SpawnWorkerRequest(
            provider_id="codex-cli",
            task="inspect README",
            repo_path=str(tmp_path),
            initial_prompt_mode="arg",
        )
    )

    assert runtime.command is not None
    assert runtime.command[-1].startswith("You are running as a delegated worker session under AgentPool.")
    assert "inspect README" in runtime.command[-1]
    assert runtime.sent_messages == []
    events = manager.store.list_events(result["session"]["id"])
    assert events[0]["metadata"]["command"][-1] == "<agentpool-initial-prompt>"


def test_artifact_manifest_materializes_result_from_observed_screen(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    )
    runtime = RecordingRuntime()
    manager = SessionManager(config=config, store=Store(tmp_path / "agentpool.sqlite"), runtime=runtime)  # type: ignore[arg-type]
    result = manager.spawn_worker(
        SpawnWorkerRequest(provider_id="fake-idle", task="inspect", repo_path=str(tmp_path))
    )
    session_id = result["session"]["id"]
    runtime.screen = """AGENTPOOL_RESULT_START
Summary: done
Findings:
- ok
AGENTPOOL_RESULT_END
"""

    observed = manager.observe_worker(session_id)
    manifest = manager.artifact_manifest(session_id)
    artifact_dir = Path(manifest["artifact_dir"])

    assert observed.event == ObserveEvent.COMPLETED
    assert (artifact_dir / "summary.md").read_text(encoding="utf-8").startswith("Summary: done")
    assert (artifact_dir / "result.md").read_text(encoding="utf-8").startswith("Summary: done")
    assert next(file for file in manifest["files"] if file["kind"] == "result")["exists"] is True


def test_spawn_persists_account_and_turn_limit(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.providers["codex-cli"].command = [sys.executable]
    runtime = RecordingRuntime()
    manager = SessionManager(config=config, runtime=runtime)  # type: ignore[arg-type]

    result = manager.spawn_worker(
        SpawnWorkerRequest(
            provider_id="codex-cli",
            task="inspect",
            repo_path=str(tmp_path),
            account="work",
            max_turns=1,
        )
    )
    session_id = result["session"]["id"]

    assert result["session"]["account"] == "work"
    assert manager.send_worker_message(session_id, "one")["ok"] is True
    with pytest.raises(ToolError) as exc:
        manager.send_worker_message(session_id, "two")
    assert exc.value.error.code == "TURN_LIMIT_REACHED"


def test_empty_submitted_message_presses_enter_without_paste_buffer(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.providers["codex-cli"].command = [sys.executable]
    runtime = RecordingRuntime()
    manager = SessionManager(config=config, runtime=runtime)  # type: ignore[arg-type]
    result = manager.spawn_worker(
        SpawnWorkerRequest(
            provider_id="codex-cli",
            task="inspect",
            repo_path=str(tmp_path),
            initial_prompt_mode="send_after_launch",
        )
    )

    manager.send_worker_message(result["session"]["id"], "", submit=True)

    assert runtime.sent_messages[-1] != ""
    assert runtime.sent_keys[-1] == ["Enter"]


def test_observe_reports_readiness_and_startup_warnings(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.providers["codex-cli"].command = [sys.executable]
    runtime = RecordingRuntime()
    manager = SessionManager(config=config, runtime=runtime)  # type: ignore[arg-type]
    result = manager.spawn_worker(
        SpawnWorkerRequest(provider_id="codex-cli", task="inspect", repo_path=str(tmp_path))
    )
    runtime.screen = "✨ Update available! 0.129.0 -> 0.130.0\n2. Skip"

    observed = manager.observe_worker(result["session"]["id"])

    assert observed.event.value == "approval_prompt"
    assert observed.metadata["readiness"] == "waiting_on_startup_prompt"
    assert observed.metadata["startup_warnings"] == ["update_available"]


def test_observe_marks_unchanged_screen_as_stuck(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.providers["codex-cli"].command = [sys.executable]
    runtime = RecordingRuntime()
    manager = SessionManager(config=config, runtime=runtime)  # type: ignore[arg-type]
    result = manager.spawn_worker(
        SpawnWorkerRequest(provider_id="codex-cli", task="inspect", repo_path=str(tmp_path))
    )
    runtime.screen = "working..."

    manager.observe_worker(result["session"]["id"])
    observed = manager.observe_worker(result["session"]["id"])

    assert observed.metadata["readiness"] == "stuck_unchanged_screen"
    assert observed.metadata["unchanged_screen"] is True


def test_spawn_runtime_deadline_terminates_on_observe(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.providers["codex-cli"].command = [sys.executable]
    runtime = RecordingRuntime()
    manager = SessionManager(config=config, runtime=runtime)  # type: ignore[arg-type]
    result = manager.spawn_worker(
        SpawnWorkerRequest(provider_id="codex-cli", task="inspect", repo_path=str(tmp_path), max_runtime_seconds=30)
    )
    session = manager.store.get_session(result["session"]["id"])
    assert session is not None
    session.metadata["deadline_at"] = (now_utc() - timedelta(seconds=1)).isoformat()
    manager.store.save_session(session)

    observed = manager.observe_worker(session.id)

    assert observed.event.value == "timeout"
    assert observed.state == SessionState.CANCELLED
    assert runtime.terminated is True


def test_collect_mark_completed_does_not_resurrect_cancelled_session(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.providers["codex-cli"].command = [sys.executable]
    runtime = RecordingRuntime()
    manager = SessionManager(config=config, runtime=runtime)  # type: ignore[arg-type]
    result = manager.spawn_worker(
        SpawnWorkerRequest(provider_id="codex-cli", task="inspect", repo_path=str(tmp_path))
    )
    session_id = result["session"]["id"]
    manager.terminate_worker(session_id, reason="test cancellation")

    collected = manager.collect_worker_artifacts(session_id, mark_completed=True)

    assert collected["state"] == SessionState.CANCELLED.value
    assert manager.store.get_session(session_id).state == SessionState.CANCELLED  # type: ignore[union-attr]


def test_reconcile_marks_dead_tmux_sessions_failed(tmp_path: Path) -> None:
    store = Store(tmp_path / "agentpool.sqlite")
    runtime = RecordingRuntime()
    runtime.exists_result = False
    manager = SessionManager(config=AgentPoolConfig(), store=store, runtime=runtime)  # type: ignore[arg-type]
    session = AgentSession(
        id="ap_dead",
        provider_id="fake-question",
        harness="fake-question",
        role="explorer",
        task="inspect",
        repo_path=str(tmp_path),
        runtime=RuntimeKind.TMUX,
        state=SessionState.RUNNING,
        created_at=now_utc(),
        updated_at=now_utc(),
        tmux=TmuxSessionRef(session_name="agentpool-dead"),
        artifact_dir=str(tmp_path / "artifacts"),
        transcript_path=str(tmp_path / "artifacts" / "transcript.txt"),
        events_path=str(tmp_path / "artifacts" / "events.jsonl"),
    )
    Path(session.events_path).parent.mkdir(parents=True, exist_ok=True)
    store.save_session(session)

    result = manager.reconcile_sessions()

    assert result["reconciled"] == ["ap_dead"]
    assert store.get_session("ap_dead").state == SessionState.FAILED  # type: ignore[union-attr]
    assert store.list_events("ap_dead")[-1]["event_type"] == "reconcile_dead_tmux"


def test_cached_usage_policy_blocks_spawn(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.providers["codex-cli"].command = [sys.executable]
    store = Store(tmp_path / "agentpool.sqlite")
    store.save_usage_snapshot(
        CapacitySnapshot(provider_id="codex-cli", status=UsageStatus.LIMIT_REACHED, confidence=Confidence.OFFICIAL)
    )
    manager = SessionManager(config=config, store=store, runtime=RecordingRuntime())  # type: ignore[arg-type]

    with pytest.raises(ToolError) as exc:
        manager.spawn_worker(SpawnWorkerRequest(provider_id="codex-cli", task="inspect", repo_path=str(tmp_path)))
    assert exc.value.error.code == "USAGE_POLICY_BLOCKED"


def test_max_parallel_sessions_is_enforced(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.policy.max_parallel_sessions = 1
    config.providers["codex-cli"].command = [sys.executable]
    manager = SessionManager(config=config, runtime=RecordingRuntime())  # type: ignore[arg-type]
    manager.spawn_worker(SpawnWorkerRequest(provider_id="codex-cli", task="one", repo_path=str(tmp_path)))

    with pytest.raises(ToolError) as exc:
        manager.spawn_worker(SpawnWorkerRequest(provider_id="codex-cli", task="two", repo_path=str(tmp_path)))

    assert exc.value.error.code == "POLICY_BLOCKED"


def test_max_parallel_sessions_is_scoped_per_mcp_coordinator(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.policy.max_parallel_sessions = 1
    config.providers["codex-cli"].command = [sys.executable]
    store = Store(tmp_path / "agentpool.sqlite")
    manager_a = SessionManager(
        config=config,
        store=store,
        runtime=RecordingRuntime(),  # type: ignore[arg-type]
        coordinator_id="coord-a",
        scope_sessions_by_coordinator=True,
    )
    manager_b = SessionManager(
        config=config,
        store=store,
        runtime=RecordingRuntime(),  # type: ignore[arg-type]
        coordinator_id="coord-b",
        scope_sessions_by_coordinator=True,
    )

    first = manager_a.spawn_worker(SpawnWorkerRequest(provider_id="codex-cli", task="one", repo_path=str(tmp_path)))
    with pytest.raises(ToolError) as exc:
        manager_a.spawn_worker(SpawnWorkerRequest(provider_id="codex-cli", task="two", repo_path=str(tmp_path)))
    second = manager_b.spawn_worker(SpawnWorkerRequest(provider_id="codex-cli", task="three", repo_path=str(tmp_path)))

    assert exc.value.error.code == "POLICY_BLOCKED"
    assert exc.value.error.details["active_sessions"][0]["id"] == first["session"]["id"]
    assert [session["id"] for session in manager_b.list_sessions()["sessions"]] == [second["session"]["id"]]
    assert {session["id"] for session in manager_b.list_sessions(include_all=True)["sessions"]} == {
        first["session"]["id"],
        second["session"]["id"],
    }


def test_list_sessions_normalizes_state_filters(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.providers["codex-cli"].command = [sys.executable]
    manager = SessionManager(config=config, runtime=RecordingRuntime())  # type: ignore[arg-type]
    result = manager.spawn_worker(SpawnWorkerRequest(provider_id="codex-cli", task="one", repo_path=str(tmp_path)))

    assert [session["id"] for session in manager.list_sessions(states="running")["sessions"]] == [
        result["session"]["id"]
    ]


def test_list_sessions_paginates(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.providers["codex-cli"].command = [sys.executable]
    config.policy.max_parallel_sessions = 5
    manager = SessionManager(config=config, runtime=RecordingRuntime())  # type: ignore[arg-type]
    first = manager.spawn_worker(SpawnWorkerRequest(provider_id="codex-cli", task="one", repo_path=str(tmp_path)))
    second = manager.spawn_worker(SpawnWorkerRequest(provider_id="codex-cli", task="two", repo_path=str(tmp_path)))
    third = manager.spawn_worker(SpawnWorkerRequest(provider_id="codex-cli", task="three", repo_path=str(tmp_path)))

    page = manager.list_sessions(limit=2, offset=1)

    assert [session["id"] for session in page["sessions"]] == [second["session"]["id"], first["session"]["id"]]
    assert page["pagination"] == {
        "limit": 2,
        "offset": 1,
        "count": 2,
        "total": 3,
        "has_more": False,
        "next_offset": None,
    }
    assert third["session"]["id"] not in [session["id"] for session in page["sessions"]]


def test_cached_usage_summary_filters_removed_provider_aliases(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    store = Store(tmp_path / "agentpool.sqlite")
    store.save_usage_snapshot(
        CapacitySnapshot(provider_id="factory-droid", status=UsageStatus.UNKNOWN, confidence=Confidence.UNKNOWN)
    )
    store.save_usage_snapshot(
        CapacitySnapshot(provider_id="droid-cli", status=UsageStatus.UNKNOWN, confidence=Confidence.UNKNOWN)
    )
    manager = SessionManager(config=config, store=store, runtime=RecordingRuntime())  # type: ignore[arg-type]

    summary = manager.usage_summary()

    provider_ids = set(summary["providers"])
    assert "droid-cli" in provider_ids
    assert "factory-droid" not in provider_ids


def test_usage_summary_auto_refresh_is_opt_in_and_bounded(tmp_path: Path) -> None:
    class RefreshingRegistry:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def descriptors(self, include_usage: bool = False, timeout_seconds: float | None = None):  # type: ignore[no-untyped-def]
            return []

        def usage(
            self,
            provider_id: str | None = None,
            *,
            backend: str = "combined",
            allow_interactive: bool = True,
            timeout_seconds: float | None = None,
        ):  # type: ignore[no-untyped-def]
            self.calls.append(
                {
                    "provider_id": provider_id,
                    "backend": backend,
                    "allow_interactive": allow_interactive,
                    "timeout_seconds": timeout_seconds,
                }
            )
            return [
                CapacitySnapshot(
                    provider_id="codex-cli",
                    status=UsageStatus.AVAILABLE,
                    confidence=Confidence.OFFICIAL,
                    windows=[
                        UsageWindow(
                            name="5h",
                            kind=UsageWindowKind.FIVE_HOUR,
                            remaining_percent=90,
                            confidence=Confidence.OFFICIAL,
                        )
                    ],
                )
            ]

    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    config.policy.usage_auto_refresh_after_seconds = 1800
    store = Store(tmp_path / "agentpool.sqlite")
    store.save_usage_snapshot(
        CapacitySnapshot(
            provider_id="codex-cli",
            status=UsageStatus.AVAILABLE,
            confidence=Confidence.OFFICIAL,
            checked_at=now_utc() - timedelta(hours=2),
            windows=[
                UsageWindow(
                    name="5h",
                    kind=UsageWindowKind.FIVE_HOUR,
                    remaining_percent=80,
                    confidence=Confidence.OFFICIAL,
                )
            ],
        )
    )
    registry = RefreshingRegistry()
    manager = SessionManager(config=config, store=store, registry=registry, runtime=RecordingRuntime())  # type: ignore[arg-type]

    summary = manager.usage_summary(provider_id="codex-cli", refresh=False, backend="native", timeout_seconds=7)

    assert summary["source"] == "live_probe"
    assert summary["backend"] == "native"
    assert summary["providers"]["codex-cli"]["usable"] is True
    assert summary["providers"]["codex-cli"]["stale"] is False
    assert registry.calls == [
        {
            "provider_id": "codex-cli",
            "backend": "native",
            "allow_interactive": True,
            "timeout_seconds": pytest.approx(7, abs=0.1),
        }
    ]


def test_failed_worktree_spawn_rolls_back_worktree_and_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# test\n", encoding="utf-8")
    _run(["git", "init"], repo)
    _run(["git", "add", "README.md"], repo)
    _run(["git", "-c", "user.name=AgentPool", "-c", "user.email=agentpool@example.com", "commit", "-m", "init"], repo)
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.storage = StorageConfig(db_path=str(tmp_path / "agentpool.sqlite"), artifact_root=str(tmp_path / "artifacts"))
    manager = SessionManager(config=config, runtime=FailingRuntime())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        manager.spawn_worker(
            SpawnWorkerRequest(
                provider_id="fake-patch",
                task="patch",
                repo_path=str(repo),
                role="implementer",
                isolation="worktree",
            )
        )

    branches = subprocess.run(["git", "branch", "--list", "agentpool/*"], cwd=repo, text=True, capture_output=True, check=False)
    assert branches.stdout.strip() == ""
    assert not any((repo.parent / ".agentpool-worktrees").glob("*"))


def test_model_arg_providers_pin_requested_model(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.providers["codex-cli"].command = ["codex"]
    adapter = build_registry(config).get("codex-cli")
    command = adapter.build_launch_command(
        SpawnWorkerRequest(provider_id="codex-cli", task="inspect", repo_path=str(tmp_path), model="gpt-5.5"),
        tmp_path,
    )

    assert command[-2:] == ["--model", "gpt-5.5"]


def test_copilot_model_args_are_forwarded_through_gh_separator(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    adapter = build_registry(config).get("copilot-cli")
    command = adapter.build_launch_command(
        SpawnWorkerRequest(provider_id="copilot-cli", task="inspect", repo_path=str(tmp_path), model="gpt-5.5"),
        tmp_path,
    )

    assert command[:3] == ["gh", "copilot", "--"]
    assert command[-4:] == ["--model", "gpt-5.5", "--mode", "plan"]


def test_cursor_model_and_read_only_args_are_forwarded(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.providers["cursor-cli"].command = ["agent"]
    adapter = build_registry(config).get("cursor-cli")
    command = adapter.build_launch_command(
        SpawnWorkerRequest(provider_id="cursor-cli", task="inspect", repo_path=str(tmp_path), model="composer-2.5"),
        tmp_path,
    )

    assert command == ["agent", "--model", "composer-2.5", "--mode", "ask", "--workspace", str(tmp_path)]


def test_droid_model_is_pinned_with_process_local_settings(tmp_path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    config.providers["droid-cli"].command = ["droid"]
    adapter = build_registry(config).get("droid-cli")
    command = adapter.build_launch_command(
        SpawnWorkerRequest(provider_id="droid-cli", task="inspect", repo_path=str(tmp_path), model="glm-5.1"),
        tmp_path,
    )

    assert "--model" not in command
    assert command[-2] == "--settings"
    settings_path = Path(command[-1])
    assert settings_path.read_text(encoding="utf-8")
    assert "glm-5.1" in settings_path.read_text(encoding="utf-8")


def test_composer_submit_keys_are_provider_specific() -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    registry = build_registry(config)

    assert registry.get("codex-cli").submit_keys() == ["C-m"]
    assert registry.get("cursor-cli").submit_keys() is None
    assert registry.get("claude-code").submit_keys() is None


def _catalog_summary(catalog: dict[str, object]) -> dict[str, object]:
    providers = catalog["providers"]
    assert isinstance(providers, dict)
    provider_ids = sorted(providers)
    summary = {
        "provider_ids": provider_ids,
        "defaults": {},
        "smoke_models": {},
        "model_counts": {},
        "critical_models": {},
        "critical_reasoning": {},
    }
    critical_models = {
        "codex-cli": ["gpt-5.5", "gpt-5.4", "gpt-5.3-codex", "gpt-5.3-codex-spark"],
        "cursor-cli": ["composer-2.5", "composer-2.5-fast", "gpt-5.4-high"],
        "droid-cli": ["glm-5.1", "kimi-k2.6", "gpt-5.5", "gemini-3-flash-preview"],
    }
    critical_reasoning = ["codex-cli:gpt-5.5", "codex-cli:gpt-5.4", "droid-cli:glm-5.1"]
    for provider_id in provider_ids:
        entry = providers[provider_id]
        assert isinstance(entry, dict)
        models = entry.get("models") or []
        assert isinstance(models, list)
        summary["defaults"][provider_id] = entry.get("default_model")
        summary["smoke_models"][provider_id] = entry.get("smoke_model")
        summary["model_counts"][provider_id] = len(models)
        model_by_id = {model["id"]: model for model in models}
        if provider_id in critical_models:
            summary["critical_models"][provider_id] = [
                model_id for model_id in critical_models[provider_id] if model_id in model_by_id
            ]
        for key in critical_reasoning:
            reason_provider, model_id = key.split(":", 1)
            if reason_provider != provider_id or model_id not in model_by_id:
                continue
            metadata = model_by_id[model_id].get("metadata") or {}
            summary["critical_reasoning"][key] = metadata.get("reasoning")
    return summary


def _run(args: list[str], cwd: Path) -> None:
    proc = subprocess.run(args, cwd=str(cwd), text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
