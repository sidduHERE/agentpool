from __future__ import annotations

import time
from pathlib import Path

from agentpool.config import AgentPoolConfig, ProviderConfig, StorageConfig
from agentpool.mcp import tools
from agentpool.mcp.resources import read_resource
from agentpool.models import (
    AuthStatus,
    CapacitySnapshot,
    Confidence,
    ProviderDescriptor,
    SpawnWorkerRequest,
    TmuxSessionRef,
    ToolError,
    UsageStatus,
)
from agentpool.providers.base import ClaudeCodeAdapter
from agentpool.providers.registry import ProviderRegistry
from agentpool.session_manager import SessionManager
from agentpool.store import Store


class RecordingRuntime:
    def __init__(self) -> None:
        self.screen = ""

    def spawn(self, command: list[str], cwd: Path, env: dict[str, str], session_name: str) -> TmuxSessionRef:
        return TmuxSessionRef(session_name=session_name)

    def send_message(self, ref: TmuxSessionRef, text: str, submit: bool = True) -> None:
        return None

    def send_keys(self, ref: TmuxSessionRef, keys: list[str]) -> None:
        return None

    def capture(self, ref: TmuxSessionRef, lines: int = 300, timeout_seconds: float | None = None) -> str:
        return self.screen

    def attach_command(self, ref: TmuxSessionRef) -> str:
        return f"tmux attach -t {ref.session_name}"

    def exists(self, ref: TmuxSessionRef) -> bool:
        return True

    def terminate(self, ref: TmuxSessionRef) -> None:
        return None


def test_mcp_inventory_and_filter_candidates(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    manager = SessionManager(config=config, store=Store(tmp_path / "agentpool.sqlite"), runtime=RecordingRuntime())  # type: ignore[arg-type]
    inventory = tools.get_inventory(manager)
    assert any(provider["id"] == "fake-question" for provider in inventory["providers"])
    filtered = tools.filter_candidates(manager, required_capabilities=["live_steering"])
    assert any(candidate["provider_id"] == "fake-question" for candidate in filtered["candidates"])


def test_mcp_cached_usage_snapshot_reads_persisted_usage(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    manager = SessionManager(config=config, store=Store(tmp_path / "agentpool.sqlite"), runtime=RecordingRuntime())  # type: ignore[arg-type]

    live = tools.get_usage_snapshot(manager, provider_id="fake-question", refresh=True)
    cached = tools.get_usage_snapshot(manager, provider_id="fake-question")
    cached_alias = tools.get_cached_usage_snapshot(manager, provider_id="fake-question")

    assert live["source"] == "live_probe"
    assert cached["source"] == "sqlite_cache"
    assert cached["snapshots"][0]["provider_id"] == "fake-question"
    assert cached["snapshots"][0]["status"] == "available"
    assert cached_alias == cached


def test_mcp_usage_snapshot_defaults_to_cached_without_live_probe(tmp_path: Path) -> None:
    class ExplodingRegistry:
        def usage(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("MCP get_usage_snapshot should not refresh by default")

        def descriptors(self, include_usage: bool = True):  # type: ignore[no-untyped-def]
            return []

    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    manager = SessionManager(
        config=config,
        store=Store(tmp_path / "agentpool.sqlite"),
        registry=ExplodingRegistry(),  # type: ignore[arg-type]
        runtime=RecordingRuntime(),  # type: ignore[arg-type]
    )

    result = tools.get_usage_snapshot(manager, provider_id="claude-code")

    assert result == {"snapshots": [], "source": "sqlite_cache"}


def test_mcp_refresh_disables_interactive_claude_usage_probe(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    adapter = ClaudeCodeAdapter(
        "claude-code",
        "Claude Code",
        "claude-code",
        ProviderConfig(binary_candidates=["/bin/echo"]),
    )
    manager = SessionManager(
        config=config,
        store=Store(tmp_path / "agentpool.sqlite"),
        registry=ProviderRegistry({"claude-code": adapter}),
        runtime=RecordingRuntime(),  # type: ignore[arg-type]
    )

    result = tools.get_usage_snapshot(manager, provider_id="claude-code", refresh=True, backend="native")
    snapshot = result["snapshots"][0]

    assert snapshot["provider_id"] == "claude-code"
    assert snapshot["status"] == UsageStatus.UNKNOWN.value
    assert snapshot["raw"]["source"] == "interactive_probe_disabled"


def test_mcp_refresh_returns_partial_on_provider_timeout(tmp_path: Path) -> None:
    class SlowUsageAdapter:
        id = "slow-cli"
        display_name = "Slow CLI"
        harness = "slow-cli"
        config = ProviderConfig(binary_candidates=["slow"])

        def detect(self) -> ProviderDescriptor:
            time.sleep(0.2)
            return ProviderDescriptor(
                id=self.id,
                display_name=self.display_name,
                harness=self.harness,
                installed=True,
                auth=AuthStatus(status="authenticated", confidence=Confidence.LOCAL_CONFIG),
            )

        def usage_snapshot(self, *, allow_interactive: bool = True) -> CapacitySnapshot:
            time.sleep(0.2)
            return CapacitySnapshot(
                provider_id=self.id,
                status=UsageStatus.AVAILABLE,
                confidence=Confidence.LOCAL_CONFIG,
            )

    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    manager = SessionManager(
        config=config,
        store=Store(tmp_path / "agentpool.sqlite"),
        registry=ProviderRegistry({"slow-cli": SlowUsageAdapter()}),  # type: ignore[arg-type]
        runtime=RecordingRuntime(),  # type: ignore[arg-type]
    )

    started = time.monotonic()
    snapshot_result = tools.get_usage_snapshot(manager, provider_id="slow-cli", refresh=True, timeout_seconds=0.01)
    summary_result = tools.get_usage_summary(manager, provider_id="slow-cli", refresh=True, timeout_seconds=0.01)
    elapsed = time.monotonic() - started

    assert elapsed < 0.15
    assert snapshot_result["partial"] is True
    assert snapshot_result["snapshots"][0]["raw"]["source"] == "agentpool_usage_timeout"
    assert summary_result["partial"] is True
    assert summary_result["providers"]["slow-cli"]["status"] == UsageStatus.UNKNOWN.value


def test_usage_summary_and_onboarding_resources(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    manager = SessionManager(config=config, store=Store(tmp_path / "agentpool.sqlite"), runtime=RecordingRuntime())  # type: ignore[arg-type]

    tools.get_usage_snapshot(manager, provider_id="fake-question", refresh=True)
    summary = tools.get_usage_summary(manager)
    capacity = tools.get_capacity_summary(manager)

    assert summary["source"] == "sqlite_cache"
    assert capacity["source"] == summary["source"]
    assert set(capacity["providers"]) == set(summary["providers"])
    assert capacity["providers"]["fake-question"]["usable"] is True
    assert summary["counts"]["available"] >= 1
    assert "fake-question" in summary["providers"]
    assert summary["preferences"]["resource_uri"] == "agentpool://preferences.md"
    assert "AgentPool Onboarding" in read_resource(manager, "agentpool://onboarding")
    assert "AgentPool Skill" in read_resource(manager, "agentpool://skill.md")
    assert "AgentPool Preferences" in read_resource(manager, "agentpool://preferences.md")


def test_mcp_provider_models_and_resources(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    manager = SessionManager(config=config, store=Store(tmp_path / "agentpool.sqlite"))

    models = tools.get_provider_models(manager, provider_id="codex-cli")
    preferences = tools.get_delegation_preferences(manager)
    validation = tools.validate_model_catalog(manager)

    assert models["providers"][0]["default_model"] == "gpt-5.5"
    assert models["preferences"]["resource_uri"] == "agentpool://preferences.md"
    assert "AgentPool Preferences" in preferences["text"]
    assert validation["ok"] is True


def test_mcp_spawn_validation_errors_are_tool_errors(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    manager = SessionManager(config=config, store=Store(tmp_path / "agentpool.sqlite"))

    try:
        tools.spawn_worker(
            manager,
            provider_id="fake-question",
            task="inspect",
            repo_path=str(tmp_path),
            role="administrator",
        )
    except ToolError as exc:
        error = tools.structured_error(exc)["error"]
    else:
        raise AssertionError("expected ToolError")

    assert error["code"] == "INVALID_REQUEST"
    assert error["details"]["errors"][0]["loc"] == ["role"]


def test_mcp_spawn_rejects_placeholder_task(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    manager = SessionManager(config=config, store=Store(tmp_path / "agentpool.sqlite"))

    try:
        tools.spawn_worker(
            manager,
            provider_id="fake-question",
            task="Improve documentation in @filename",
            repo_path=str(tmp_path),
        )
    except ToolError as exc:
        error = tools.structured_error(exc)["error"]
    else:
        raise AssertionError("expected ToolError")

    assert error["code"] == "INVALID_REQUEST"
    assert "placeholder" in error["details"]["errors"][0]["msg"].lower()


def test_mcp_lease_release_validation_errors_are_tool_errors(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    manager = SessionManager(config=config, store=Store(tmp_path / "agentpool.sqlite"))

    try:
        tools.release_file_lease(manager)
    except ToolError as exc:
        error = tools.structured_error(exc)["error"]
    else:
        raise AssertionError("expected ToolError")

    assert error["code"] == "INVALID_LEASE_RELEASE"


def test_mcp_send_worker_message(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    manager = SessionManager(config=config, store=Store(tmp_path / "agentpool.sqlite"), runtime=RecordingRuntime())  # type: ignore[arg-type]
    result = manager.spawn_worker(
        SpawnWorkerRequest(provider_id="fake-idle", task="inspect", repo_path=str(tmp_path))
    )

    response = tools.send_worker_message(manager, result["session"]["id"], "Continue")

    assert response["ok"] is True


def test_mcp_poll_worker_returns_recent_log_tail_and_partial_summary(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    runtime = RecordingRuntime()
    manager = SessionManager(
        config=config,
        store=Store(tmp_path / "agentpool.sqlite"),
        runtime=runtime,  # type: ignore[arg-type]
    )
    result = manager.spawn_worker(
        SpawnWorkerRequest(provider_id="fake-idle", task="inspect", repo_path=str(tmp_path))
    )
    session_id = result["session"]["id"]
    runtime.screen = "progress line 1\nprogress line 2\nstill running"

    payload = tools.poll_worker(manager, session_id, detail="summary", include_recent_log=True)
    artifact_files = {file["kind"]: file for file in payload["artifact_manifest"]["files"]}
    partial_path = Path(artifact_files["summary_partial"]["path"])

    assert payload["event"] == "none"
    assert payload["worker_output"]["included"] is True
    assert "still running" in payload["worker_output"]["text"]
    assert artifact_files["summary_partial"]["exists"] is True
    assert "still running" in partial_path.read_text(encoding="utf-8")


def test_mcp_observe_caps_long_waits_before_outer_executor_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    runtime = RecordingRuntime()
    manager = SessionManager(
        config=config,
        store=Store(tmp_path / "agentpool.sqlite"),
        runtime=runtime,  # type: ignore[arg-type]
    )
    result = manager.spawn_worker(
        SpawnWorkerRequest(provider_id="fake-idle", task="inspect", repo_path=str(tmp_path))
    )
    runtime.screen = "worker is thinking"
    monkeypatch.setattr(tools, "MCP_OBSERVE_MAX_WAIT_SECONDS", 0.05)
    started = time.monotonic()

    payload = tools.observe_worker(
        manager,
        result["session"]["id"],
        wait_for=["COMPLETED"],
        timeout_seconds=300,
        include_recent_log=True,
    )

    assert time.monotonic() - started < 0.5
    assert payload["event"] == "timeout"
    assert payload["metadata"]["requested_timeout_seconds"] == 300
    assert payload["metadata"]["effective_timeout_seconds"] == 0.05
    assert payload["metadata"]["observe_timeout_reason"] == "mcp_outer_timeout_guard"
    assert payload["worker_output"]["included"] is True


def test_mcp_observe_timeout_one_is_fast_poll(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    runtime = RecordingRuntime()
    manager = SessionManager(
        config=config,
        store=Store(tmp_path / "agentpool.sqlite"),
        runtime=runtime,  # type: ignore[arg-type]
    )
    result = manager.spawn_worker(
        SpawnWorkerRequest(provider_id="fake-idle", task="inspect", repo_path=str(tmp_path))
    )
    runtime.screen = "quick state"
    started = time.monotonic()

    payload = tools.observe_worker(
        manager,
        result["session"]["id"],
        wait_for=["COMPLETED"],
        timeout_seconds=1,
        include_recent_log=True,
    )

    assert time.monotonic() - started < 0.2
    assert payload["metadata"]["observe_timeout_reason"] == "fast_poll"
    assert payload["event"] == "none"
