from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from agentpool.models import (
    AgentSession,
    CapacitySnapshot,
    Confidence,
    RuntimeKind,
    SessionState,
    UsageStatus,
    UsageWindow,
    UsageWindowKind,
    now_utc,
)
from agentpool.store import Store

PROVIDERS = ("claude-code", "codex-cli", "cursor-cli")
ROLES = ("explorer", "implementer", "reviewer", "tester")
STATES = (
    SessionState.COMPLETED,
    SessionState.COMPLETED,
    SessionState.FAILED,
    SessionState.RUNNING,
    SessionState.CANCELLED,
)
EVENT_TYPES = ("spawn", "observe", "steer", "terminate", "interrupt", "timeout")


def seed(db_path: str) -> None:
    store = Store(Path(db_path))
    end = now_utc()
    base = end - timedelta(days=6, hours=2)
    repo = "/tmp/agentpool-stats-seed"
    spawn_times: list[tuple[str, str, datetime]] = []

    for index in range(50):
        provider = PROVIDERS[index % len(PROVIDERS)]
        day_offset = index % 6
        hour_slot = (index // 6) % 4
        created = base + timedelta(days=day_offset, hours=hour_slot * 2, minutes=(index % 5) * 11)
        overlap_group = index // 10
        if index % 10 < 3:
            created = base + timedelta(days=day_offset, hours=10) + timedelta(minutes=overlap_group * 15 + index % 3)
        ended = created + timedelta(hours=1 + (index % 3), minutes=20)
        session_id = f"ap_seed_{index:03d}"
        artifact_dir = f"/tmp/agentpool-artifacts/{session_id}"
        session = AgentSession(
            id=session_id,
            provider_id=provider,
            harness=provider,
            role=ROLES[index % len(ROLES)],
            task=f"seed task {index}",
            repo_path=repo,
            runtime=RuntimeKind.TMUX,
            state=STATES[index % len(STATES)],
            created_at=created,
            updated_at=ended,
            ended_at=ended if STATES[index % len(STATES)] != SessionState.RUNNING else None,
            artifact_dir=artifact_dir,
            transcript_path=f"{artifact_dir}/transcript.txt",
            events_path=f"{artifact_dir}/events.jsonl",
            metadata={"seed": True, "coordinator_id": f"coord_seed_{index % 4}"},
        )
        store.save_session(session)

        event_ts = created
        for event_index, event_type in enumerate(EVENT_TYPES):
            if event_index >= 4 and index % 3 != 0:
                break
            _insert_event(
                store,
                session_id=session_id,
                ts=event_ts,
                event_type=event_type,
                state=session.state.value,
            )
            if event_type == "spawn":
                spawn_times.append((session_id, provider, event_ts))
            event_ts += timedelta(minutes=5 + event_index)

    snapshot_providers = list(PROVIDERS) + ["fake-question"]
    snapshot_index = 0
    saved = 0
    while saved < 100:
        provider = snapshot_providers[snapshot_index % len(snapshot_providers)]
        snapshot_index += 1
        if provider == "cursor-cli" and snapshot_index % 9 == 0:
            continue
        if provider == "fake-question" and snapshot_index % 4 != 0:
            continue
        checked_at = base + timedelta(hours=saved * 3)
        remaining = 90 - (saved % 40)
        snapshot = CapacitySnapshot(
            provider_id=provider,
            status=UsageStatus.NEAR_LIMIT if remaining < 15 else UsageStatus.AVAILABLE,
            confidence=Confidence.OFFICIAL,
            checked_at=checked_at,
            windows=[
                UsageWindow(
                    name="5h",
                    kind=UsageWindowKind.FIVE_HOUR,
                    remaining_percent=float(remaining),
                    used_percent=float(100 - remaining),
                    confidence=Confidence.OFFICIAL,
                ),
                UsageWindow(
                    name="weekly",
                    kind=UsageWindowKind.WEEKLY,
                    remaining_percent=float(min(100, remaining + 10)),
                    used_percent=float(max(0, 90 - remaining)),
                    confidence=Confidence.OFFICIAL,
                ),
            ],
            raw={"seed_index": saved},
        )
        if provider == "claude-code" and saved % 11 == 0:
            snapshot = snapshot.model_copy(
                update={
                    "status": UsageStatus.LIMIT_REACHED,
                    "windows": [
                        UsageWindow(
                            name="5h",
                            kind=UsageWindowKind.FIVE_HOUR,
                            remaining_percent=2.0,
                            used_percent=98.0,
                            confidence=Confidence.OFFICIAL,
                        )
                    ],
                }
            )
        store.save_usage_snapshot(snapshot)
        saved += 1

    for session_id, provider, spawn_at in spawn_times:
        if provider != "codex-cli" or hash(session_id) % 3 != 0:
            continue
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
            checked_at=spawn_at - timedelta(minutes=4),
            remaining_percent=80.0,
            status=UsageStatus.AVAILABLE,
            token_counts={"input": 12000 + hash(session_id) % 5000, "output": 3000},
        )
        _save_snapshot(
            store,
            provider_id="cursor-cli",
            checked_at=spawn_at - timedelta(minutes=3),
            remaining_percent=45.0,
            status=UsageStatus.AVAILABLE,
        )


def _save_snapshot(
    store: Store,
    *,
    provider_id: str,
    checked_at: datetime,
    remaining_percent: float,
    status: UsageStatus,
    token_counts: dict[str, int] | None = None,
) -> None:
    raw = {"token_counts": token_counts} if token_counts else {"seed": True}
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
            raw=raw,
        )
    )


def _insert_event(
    store: Store,
    *,
    session_id: str,
    ts: datetime,
    event_type: str,
    state: str | None = None,
    metadata: dict | None = None,
) -> None:
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO events (session_id, ts, event_type, state, screen_hash, excerpt, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                ts.isoformat(),
                event_type,
                state,
                None,
                None,
                json.dumps(metadata or {}),
            ),
        )
