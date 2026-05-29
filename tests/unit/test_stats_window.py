from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentpool.config import AgentPoolConfig, PolicyConfig, ProviderConfig, StorageConfig
from agentpool.models import (
    AgentSession,
    AuthStatus,
    CapacitySnapshot,
    Confidence,
    ProviderDescriptor,
    RuntimeKind,
    SessionState,
    ToolError,
    UsageStatus,
    UsageWindow,
    UsageWindowKind,
)
from agentpool.providers.registry import build_registry
from agentpool.stats import STATS_SCHEMA_VERSION
from agentpool.stats.compute import compute_stats
from agentpool.stats.window import parse_window
from agentpool.store import Store

NOW = datetime(2026, 5, 24, 18, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("spec", "delta"),
    [
        ("7d", timedelta(days=7)),
        ("30d", timedelta(days=30)),
        ("12h", timedelta(hours=12)),
        ("1w", timedelta(weeks=1)),
    ],
)
def test_parse_window_duration_specs(spec: str, delta: timedelta) -> None:
    window = parse_window(spec, now=NOW)
    assert window.end == NOW
    assert window.start == NOW - delta
    assert window.spec == spec


def test_parse_window_iso_date() -> None:
    window = parse_window("2026-05-01", now=NOW)
    assert window.start == datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert window.end == datetime(2026, 5, 2, tzinfo=timezone.utc)
    assert window.label == "2026-05-01"


def test_parse_window_iso_interval() -> None:
    window = parse_window("2026-05-01/2026-05-08", now=NOW)
    assert window.start == datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert window.end == datetime(2026, 5, 8, tzinfo=timezone.utc)
    assert window.label == "2026-05-01 to 2026-05-08"


def test_parse_window_all_starts_at_epoch() -> None:
    window = parse_window("all", now=NOW)
    assert window.start == datetime.fromtimestamp(0, tz=timezone.utc)
    assert window.end == NOW
    assert window.spec == "all"


@pytest.mark.parametrize("spec", ["", "bogus", "not-a-window"])
def test_parse_window_invalid_specs_raise(spec: str) -> None:
    with pytest.raises(ToolError) as exc:
        parse_window(spec, now=NOW)
    assert exc.value.error.code == "INVALID_WINDOW"


def test_empty_store_returns_valid_stats_schema(tmp_path) -> None:
    store = Store(tmp_path / "agentpool.sqlite")
    stats = compute_stats(
        store=store,
        config=_stats_config(tmp_path),
        registry=build_registry(_stats_config(tmp_path)),
        window=parse_window("7d", now=NOW),
    )

    assert stats["schema_version"] == STATS_SCHEMA_VERSION
    assert stats["sessions"]["total"] == 0
    assert stats["parallelism"]["ratio"] is None
    assert stats["walls"]["hit"] is None
    assert stats["walls"]["avoided"] is None
    assert isinstance(stats["data_quality"], list)


def test_parallelism_overlapping_workers_ratio_near_three(tmp_path) -> None:
    store = Store(tmp_path / "agentpool.sqlite")
    start = NOW - timedelta(hours=2)
    end = NOW - timedelta(hours=1)
    for index in range(3):
        _save_session(
            store,
            tmp_path,
            session_id=f"ap_overlap_{index}",
            provider_id="codex-cli",
            created_at=start,
            ended_at=end,
        )

    stats = compute_stats(
        store=store,
        config=_stats_config(tmp_path),
        registry=build_registry(_stats_config(tmp_path)),
        window=parse_window("7d", now=NOW),
    )

    assert stats["parallelism"]["peak_concurrent"] == 3
    assert stats["parallelism"]["ratio"] == pytest.approx(3.0, abs=0.01)


def test_parallelism_sequential_workers_ratio_near_one(tmp_path) -> None:
    store = Store(tmp_path / "agentpool.sqlite")
    start = NOW - timedelta(hours=6)
    for index in range(3):
        created = start + timedelta(hours=index)
        ended = created + timedelta(hours=1)
        _save_session(
            store,
            tmp_path,
            session_id=f"ap_seq_{index}",
            provider_id="codex-cli",
            created_at=created,
            ended_at=ended,
        )

    stats = compute_stats(
        store=store,
        config=_stats_config(tmp_path),
        registry=build_registry(_stats_config(tmp_path)),
        window=parse_window("7d", now=NOW),
    )

    assert stats["parallelism"]["ratio"] == pytest.approx(1.0, abs=0.01)


def test_walls_avoided_when_usable_provider_spawns_against_blocked_neighbor(tmp_path) -> None:
    store = Store(tmp_path / "agentpool.sqlite")
    spawn_at = NOW - timedelta(hours=1)
    session_id = "ap_wall_avoided"
    _save_session(
        store,
        tmp_path,
        session_id=session_id,
        provider_id="codex-cli",
        created_at=spawn_at,
        ended_at=spawn_at + timedelta(minutes=30),
    )
    _insert_spawn_event(store, session_id=session_id, ts=spawn_at, provider_id="codex-cli")
    _save_snapshot(
        store,
        provider_id="claude-code",
        checked_at=spawn_at - timedelta(minutes=5),
        remaining_percent=5.0,
        status=UsageStatus.LIMIT_REACHED,
    )
    _save_snapshot(
        store,
        provider_id="codex-cli",
        checked_at=spawn_at - timedelta(minutes=5),
        remaining_percent=80.0,
        status=UsageStatus.AVAILABLE,
    )

    config = _walls_config(tmp_path)
    registry = build_registry(config)
    registry.descriptors = lambda include_usage=True: [  # type: ignore[method-assign, assignment]
        _descriptor("claude-code"),
        _descriptor("codex-cli"),
    ]

    stats = compute_stats(
        store=store,
        config=config,
        registry=registry,
        window=parse_window("7d", now=NOW),
    )

    assert stats["walls"]["avoided"] == 1
    assert stats["walls"]["by_provider"]["codex-cli"]["avoided"] == 1


def test_walls_without_snapshots_report_null_hit(tmp_path) -> None:
    store = Store(tmp_path / "agentpool.sqlite")
    spawn_at = NOW - timedelta(hours=1)
    session_id = "ap_wall_none"
    _save_session(
        store,
        tmp_path,
        session_id=session_id,
        provider_id="codex-cli",
        created_at=spawn_at,
        ended_at=spawn_at + timedelta(minutes=20),
    )
    _insert_spawn_event(store, session_id=session_id, ts=spawn_at, provider_id="codex-cli")

    stats = compute_stats(
        store=store,
        config=_walls_config(tmp_path),
        registry=build_registry(_walls_config(tmp_path)),
        window=parse_window("7d", now=NOW),
    )

    assert stats["walls"]["hit"] is None
    assert stats["walls"]["avoided"] is None
    assert any(item["code"] == "no_usage_data_in_window" for item in stats["data_quality"])


def test_stats_schema_constant() -> None:
    assert STATS_SCHEMA_VERSION == "stats/v1"


def _stats_config(tmp_path) -> AgentPoolConfig:
    return AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )


def _walls_config(tmp_path) -> AgentPoolConfig:
    return AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        ),
        providers={
            "claude-code": ProviderConfig(),
            "codex-cli": ProviderConfig(),
        },
        policy=PolicyConfig(min_remaining_percent=10, usage_stale_after_seconds=1800),
    )


def _descriptor(provider_id: str) -> ProviderDescriptor:
    return ProviderDescriptor(
        id=provider_id,
        display_name=provider_id,
        harness=provider_id,
        installed=True,
        auth=AuthStatus(status="authenticated", confidence=Confidence.LOCAL_CONFIG),
    )


def _save_session(
    store: Store,
    tmp_path,
    *,
    session_id: str,
    provider_id: str,
    created_at: datetime,
    ended_at: datetime | None,
) -> None:
    artifact_dir = tmp_path / "artifacts" / session_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    store.save_session(
        AgentSession(
            id=session_id,
            provider_id=provider_id,
            harness=provider_id,
            role="explorer",
            task="inspect stats",
            repo_path=str(tmp_path),
            runtime=RuntimeKind.TMUX,
            state=SessionState.COMPLETED if ended_at else SessionState.RUNNING,
            created_at=created_at,
            updated_at=ended_at or created_at,
            ended_at=ended_at,
            artifact_dir=str(artifact_dir),
            transcript_path=str(artifact_dir / "transcript.txt"),
            events_path=str(artifact_dir / "events.jsonl"),
        )
    )


def _insert_spawn_event(store: Store, *, session_id: str, ts: datetime, provider_id: str) -> None:
    import json

    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO events (session_id, ts, event_type, state, screen_hash, excerpt, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                ts.isoformat(),
                "spawn",
                SessionState.RUNNING.value,
                None,
                None,
                json.dumps({"provider_id": provider_id}),
            ),
        )


def _save_snapshot(
    store: Store,
    *,
    provider_id: str,
    checked_at: datetime,
    remaining_percent: float,
    status: UsageStatus,
) -> None:
    store.save_usage_snapshot(
        CapacitySnapshot(
            provider_id=provider_id,
            status=status,
            confidence=Confidence.OFFICIAL,
            checked_at=checked_at,
            windows=[
                UsageWindow(
                    name="5h",
                    kind=UsageWindowKind.FIVE_HOUR,
                    remaining_percent=remaining_percent,
                    used_percent=100.0 - remaining_percent,
                    confidence=Confidence.OFFICIAL,
                )
            ],
        )
    )
