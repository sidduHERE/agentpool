from __future__ import annotations

from pathlib import Path

from agentpool.config import AgentPoolConfig, ProviderConfig, StorageConfig
from agentpool.mcp import tools
from agentpool.mcp.resources import read_resource
from agentpool.models import SpawnWorkerRequest, TmuxSessionRef, ToolError, UsageStatus
from agentpool.providers.base import ClaudeCodeAdapter
from agentpool.providers.registry import ProviderRegistry
from agentpool.session_manager import SessionManager
from agentpool.store import Store


class RecordingRuntime:
    def spawn(self, command: list[str], cwd: Path, env: dict[str, str], session_name: str) -> TmuxSessionRef:
        return TmuxSessionRef(session_name=session_name)

    def send_message(self, ref: TmuxSessionRef, text: str, submit: bool = True) -> None:
        return None

    def send_keys(self, ref: TmuxSessionRef, keys: list[str]) -> None:
        return None

    def capture(self, ref: TmuxSessionRef, lines: int = 300) -> str:
        return ""

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

    assert live["source"] == "live_probe"
    assert cached["source"] == "sqlite_cache"
    assert cached["snapshots"][0]["provider_id"] == "fake-question"
    assert cached["snapshots"][0]["status"] == "available"


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

    assert summary["source"] == "sqlite_cache"
    assert summary["counts"]["available"] >= 1
    assert "fake-question" in summary["providers"]
    assert "AgentPool Onboarding" in read_resource(manager, "agentpool://onboarding")
    assert "AgentPool Skill" in read_resource(manager, "agentpool://skill.md")


def test_mcp_provider_models_and_resources(tmp_path: Path) -> None:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    manager = SessionManager(config=config, store=Store(tmp_path / "agentpool.sqlite"))

    models = tools.get_provider_models(manager, provider_id="codex-cli")
    validation = tools.validate_model_catalog(manager)

    assert models["providers"][0]["default_model"] == "gpt-5.5"
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
