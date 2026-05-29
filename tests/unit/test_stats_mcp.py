from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentpool.config import AgentPoolConfig, StorageConfig
from agentpool.mcp import tools
from agentpool.models import AgentSession, RuntimeKind, SessionState
from agentpool.providers.registry import build_registry
from agentpool.session_manager import SessionManager
from agentpool.stats.compute import compute_stats
from agentpool.stats.window import parse_window
from agentpool.store import Store

NOW = datetime(2026, 5, 24, 18, 0, tzinfo=timezone.utc)


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


def _manager(tmp_path, *, coordinator_id: str = "coord_test", scope_sessions_by_coordinator: bool = False) -> SessionManager:
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
        coordinator_id=coordinator_id,
        scope_sessions_by_coordinator=scope_sessions_by_coordinator,
    )


def test_get_stats_matches_compute_stats_shape(tmp_path) -> None:
    manager = _manager(tmp_path)
    window = parse_window("7d")
    expected = compute_stats(
        store=manager.store,
        config=manager.config,
        registry=manager.registry,
        window=window,
        scope="mine",
        coordinator_id=manager.coordinator_id,
    )

    actual = tools.get_stats(manager, window="7d", scope="mine")

    assert actual["schema_version"] == expected["schema_version"]
    assert set(actual.keys()) == set(expected.keys())
    assert actual["window"]["spec"] == expected["window"]["spec"]
    assert actual["window"]["label"] == expected["window"]["label"]
    assert actual["sessions"] == expected["sessions"]


def test_get_stats_sections_filter_returns_only_requested_sections(tmp_path) -> None:
    manager = _manager(tmp_path)

    stats = tools.get_stats(manager, window="7d", sections=["walls"], scope="mine")

    assert stats["schema_version"] == "stats/v1"
    assert "walls" in stats
    assert "parallelism" not in stats
    assert "quota" not in stats
    assert "window" in stats
    assert "scope" in stats
    assert "data_quality" in stats


def test_get_stats_scope_mine_filters_by_coordinator(tmp_path) -> None:
    store = Store(tmp_path / "agentpool.sqlite")
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    manager = SessionManager(
        config=config,
        store=store,
        runtime=RecordingRuntime(),  # type: ignore[arg-type]
        coordinator_id="coord_mine",
        scope_sessions_by_coordinator=True,
    )
    _save_session(
        store,
        tmp_path,
        session_id="ap_mine",
        coordinator_id="coord_mine",
        created_at=NOW - timedelta(hours=2),
    )
    _save_session(
        store,
        tmp_path,
        session_id="ap_other",
        coordinator_id="coord_other",
        created_at=NOW - timedelta(hours=2),
    )

    mine = tools.get_stats(manager, window="7d", scope="mine")
    all_stats = tools.get_stats(manager, window="7d", scope="all")

    assert mine["scope"] == "mine"
    assert mine["coordinator_id"] == "coord_mine"
    assert mine["sessions"]["total"] == 1
    assert all_stats["scope"] == "all"
    assert "coordinator_id" not in all_stats
    assert all_stats["sessions"]["total"] == 2


def _save_session(
    store: Store,
    tmp_path,
    *,
    session_id: str,
    coordinator_id: str,
    created_at: datetime,
) -> None:
    artifact_dir = tmp_path / "artifacts" / session_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    store.save_session(
        AgentSession(
            id=session_id,
            provider_id="fake-question",
            harness="fake-question",
            role="explorer",
            task="inspect stats scope",
            repo_path=str(tmp_path),
            runtime=RuntimeKind.TMUX,
            state=SessionState.COMPLETED,
            created_at=created_at,
            updated_at=created_at + timedelta(minutes=10),
            ended_at=created_at + timedelta(minutes=10),
            artifact_dir=str(artifact_dir),
            transcript_path=str(artifact_dir / "transcript.txt"),
            events_path=str(artifact_dir / "events.jsonl"),
            metadata={"coordinator_id": coordinator_id},
        )
    )
