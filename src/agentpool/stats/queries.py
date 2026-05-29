from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from agentpool.models import AgentSession, CapacitySnapshot
from agentpool.stats.window import Window
from agentpool.store import Store


def list_sessions_in_window(
    store: Store,
    window: Window,
    provider_id: str | None = None,
    coordinator_id: str | None = None,
    scope_mine: bool = False,
) -> list[AgentSession]:
    clauses = ["created_at >= ?", "created_at < ?"]
    args: list[Any] = [window.start.isoformat(), window.end.isoformat()]
    if provider_id:
        clauses.append("provider_id = ?")
        args.append(provider_id)
    where = " WHERE " + " AND ".join(clauses)
    with store.connect() as conn:
        rows = conn.execute(f"SELECT * FROM sessions{where} ORDER BY created_at ASC", args).fetchall()
    sessions = [store._row_to_session(row) for row in rows]
    if scope_mine and coordinator_id:
        sessions = [
            session
            for session in sessions
            if (session.metadata or {}).get("coordinator_id") == coordinator_id
        ]
    return sessions


def list_events_in_window(
    store: Store,
    window: Window,
    event_types: list[str] | None = None,
    session_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if session_ids is not None and not session_ids:
        return []
    clauses = ["e.ts >= ?", "e.ts < ?"]
    args: list[Any] = [window.start.isoformat(), window.end.isoformat()]
    if event_types:
        clauses.append(f"e.event_type IN ({','.join('?' for _ in event_types)})")
        args.extend(event_types)
    if session_ids:
        clauses.append(f"e.session_id IN ({','.join('?' for _ in session_ids)})")
        args.extend(sorted(session_ids))
    where = " WHERE " + " AND ".join(clauses)
    with store.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT e.*, s.provider_id AS session_provider_id, s.metadata_json AS session_metadata_json
            FROM events e
            JOIN sessions s ON s.id = e.session_id
            {where}
            ORDER BY e.ts ASC, e.id ASC
            """,
            args,
        ).fetchall()
    return [
        {
            "id": row["id"],
            "session_id": row["session_id"],
            "ts": row["ts"],
            "event_type": row["event_type"],
            "state": row["state"],
            "metadata": json.loads(row["metadata_json"]),
            "provider_id": row["session_provider_id"],
            "session_metadata": json.loads(row["session_metadata_json"]),
        }
        for row in rows
    ]


def usage_snapshot_at_or_before(
    store: Store,
    provider_id: str,
    ts: datetime,
    max_age_seconds: float,
) -> CapacitySnapshot | None:
    with store.connect() as conn:
        row = conn.execute(
            """
            SELECT raw_json, ts
            FROM usage_snapshots
            WHERE provider_id = ? AND ts <= ?
            ORDER BY ts DESC, id DESC
            LIMIT 1
            """,
            (provider_id, ts.isoformat()),
        ).fetchone()
    if row is None:
        return None
    snapshot_ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
    if snapshot_ts.tzinfo is None:
        snapshot_ts = snapshot_ts.replace(tzinfo=timezone.utc)
    age = (ts - snapshot_ts).total_seconds()
    if age > max_age_seconds:
        return None
    return CapacitySnapshot.model_validate_json(row["raw_json"])


def usage_snapshots_in_window(store: Store, window: Window, provider_id: str | None = None) -> list[CapacitySnapshot]:
    clauses = ["ts >= ?", "ts < ?"]
    args: list[Any] = [window.start.isoformat(), window.end.isoformat()]
    if provider_id:
        clauses.append("provider_id = ?")
        args.append(provider_id)
    where = " WHERE " + " AND ".join(clauses)
    with store.connect() as conn:
        rows = conn.execute(
            f"SELECT raw_json FROM usage_snapshots{where} ORDER BY ts ASC, id ASC",
            args,
        ).fetchall()
    return [CapacitySnapshot.model_validate_json(row["raw_json"]) for row in rows]


def has_any_usage_snapshots(store: Store) -> bool:
    with store.connect() as conn:
        row = conn.execute("SELECT 1 FROM usage_snapshots LIMIT 1").fetchone()
    return row is not None


def count_usage_snapshots_in_window(store: Store, window: Window, provider_id: str | None = None) -> int:
    clauses = ["ts >= ?", "ts < ?"]
    args: list[Any] = [window.start.isoformat(), window.end.isoformat()]
    if provider_id:
        clauses.append("provider_id = ?")
        args.append(provider_id)
    where = " WHERE " + " AND ".join(clauses)
    with store.connect() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM usage_snapshots{where}", args).fetchone()
    return int(row["count"]) if row else 0
