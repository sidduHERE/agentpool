from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from agentpool.models import AgentSession, ArtifactRecord, CapacitySnapshot, FileLease, SessionState, TmuxSessionRef, ToolError
from agentpool.utils import utc_now_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL,
  model TEXT,
  harness TEXT NOT NULL,
  account TEXT,
  role TEXT NOT NULL,
  task TEXT NOT NULL,
  repo_path TEXT NOT NULL,
  worktree_path TEXT,
  runtime TEXT NOT NULL,
  state TEXT NOT NULL,
  tmux_session TEXT,
  tmux_window TEXT,
  tmux_pane TEXT,
  artifact_dir TEXT NOT NULL,
  transcript_path TEXT NOT NULL,
  events_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  ended_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  event_type TEXT NOT NULL,
  state TEXT,
  screen_hash TEXT,
  excerpt TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS usage_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  status TEXT NOT NULL,
  confidence TEXT NOT NULL,
  raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS file_leases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  repo_path TEXT NOT NULL,
  file_path TEXT NOT NULL,
  mode TEXT NOT NULL,
  expires_at TEXT,
  created_at TEXT NOT NULL,
  released_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sessions_provider_state ON sessions(provider_id, state);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_usage_snapshots_provider_id_desc ON usage_snapshots(provider_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_file_leases_repo_file ON file_leases(repo_path, file_path);
"""


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def save_session(self, session: AgentSession) -> None:
        tmux = session.tmux
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                  id, provider_id, model, harness, account, role, task, repo_path,
                  worktree_path, runtime, state, tmux_session, tmux_window, tmux_pane,
                  artifact_dir, transcript_path, events_path, created_at, updated_at,
                  ended_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  model=excluded.model,
                  account=excluded.account,
                  state=excluded.state,
                  worktree_path=excluded.worktree_path,
                  tmux_session=excluded.tmux_session,
                  tmux_window=excluded.tmux_window,
                  tmux_pane=excluded.tmux_pane,
                  updated_at=excluded.updated_at,
                  ended_at=excluded.ended_at,
                  metadata_json=excluded.metadata_json
                """,
                (
                    session.id,
                    session.provider_id,
                    session.model,
                    session.harness,
                    session.account,
                    session.role,
                    session.task,
                    session.repo_path,
                    session.worktree_path,
                    session.runtime.value if hasattr(session.runtime, "value") else session.runtime,
                    session.state.value if hasattr(session.state, "value") else session.state,
                    tmux.session_name if tmux else None,
                    tmux.window if tmux else None,
                    tmux.pane if tmux else None,
                    session.artifact_dir,
                    session.transcript_path,
                    session.events_path,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                    session.ended_at.isoformat() if session.ended_at else None,
                    json.dumps(session.metadata),
                ),
            )

    def get_session(self, session_id: str) -> AgentSession | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(
        self,
        states: list[str] | None = None,
        provider_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AgentSession]:
        query, args = self._session_query(states, provider_id)
        if limit is not None:
            query = f"{query} LIMIT ? OFFSET ?"
            args.extend([limit, offset])
        with self.connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [self._row_to_session(row) for row in rows]

    def count_sessions(self, states: list[str] | None = None, provider_id: str | None = None) -> int:
        query, args = self._session_query(states, provider_id, select="COUNT(*)")
        with self.connect() as conn:
            return int(conn.execute(query, args).fetchone()[0])

    def _session_query(
        self,
        states: list[str] | None = None,
        provider_id: str | None = None,
        select: str = "*",
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if states:
            clauses.append(f"state IN ({','.join('?' for _ in states)})")
            args.extend(states)
        if provider_id:
            clauses.append("provider_id = ?")
            args.append(provider_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        order = "" if select != "*" else " ORDER BY created_at DESC"
        return f"SELECT {select} FROM sessions{where}{order}", args

    def update_session_state(self, session_id: str, state: SessionState, ended_at: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET state = ?, updated_at = ?, ended_at = COALESCE(?, ended_at) WHERE id = ?",
                (state.value, utc_now_iso(), ended_at, session_id),
            )

    def append_event(
        self,
        session_id: str,
        event_type: str,
        state: str | None = None,
        screen_hash: str | None = None,
        excerpt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events (session_id, ts, event_type, state, screen_hash, excerpt, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    utc_now_iso(),
                    event_type,
                    state,
                    screen_hash,
                    excerpt,
                    json.dumps(metadata or {}),
                ),
            )
            return int(cursor.lastrowid)

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE session_id = ? ORDER BY id ASC", (session_id,)
            ).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "ts": row["ts"],
                "event_type": row["event_type"],
                "state": row["state"],
                "screen_hash": row["screen_hash"],
                "excerpt": row["excerpt"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def save_usage_snapshot(self, snapshot: CapacitySnapshot) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_snapshots (provider_id, ts, status, confidence, raw_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.provider_id,
                    snapshot.checked_at.isoformat(),
                    snapshot.status.value if hasattr(snapshot.status, "value") else snapshot.status,
                    snapshot.confidence.value if hasattr(snapshot.confidence, "value") else snapshot.confidence,
                    snapshot.model_dump_json(),
                ),
            )

    def latest_usage_snapshots(self, provider_id: str | None = None) -> list[CapacitySnapshot]:
        clauses: list[str] = []
        args: list[Any] = []
        if provider_id:
            clauses.append("provider_id = ?")
            args.append(provider_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT u.*
                FROM usage_snapshots u
                JOIN (
                  SELECT provider_id, MAX(id) AS max_id
                  FROM usage_snapshots{where}
                  GROUP BY provider_id
                ) latest
                ON u.provider_id = latest.provider_id AND u.id = latest.max_id
                ORDER BY u.provider_id ASC
                """,
                args,
            ).fetchall()
        return [CapacitySnapshot.model_validate_json(row["raw_json"]) for row in rows]

    def save_artifact(self, session_id: str, artifact: ArtifactRecord) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO artifacts (session_id, kind, path, sha256, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    artifact.kind,
                    artifact.path,
                    artifact.sha256,
                    json.dumps(artifact.metadata),
                    utc_now_iso(),
                ),
            )
            return int(cursor.lastrowid)

    def list_artifacts(self, session_id: str) -> list[ArtifactRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE session_id = ? ORDER BY id ASC", (session_id,)
            ).fetchall()
        return [
            ArtifactRecord(
                kind=row["kind"],
                path=row["path"],
                sha256=row["sha256"],
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        ]

    def acquire_file_lease(
        self,
        session_id: str,
        repo_path: str,
        file_path: str,
        mode: str = "write",
        expires_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FileLease:
        now = utc_now_iso()
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM file_leases
                WHERE repo_path = ?
                  AND file_path = ?
                  AND released_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND session_id = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (repo_path, file_path, now, session_id),
            ).fetchone()
            if existing:
                return self._row_to_lease(existing)
            conflict = conn.execute(
                """
                SELECT * FROM file_leases
                WHERE repo_path = ?
                  AND file_path = ?
                  AND released_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND session_id != ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (repo_path, file_path, now, session_id),
            ).fetchone()
            if conflict:
                lease = self._row_to_lease(conflict)
                raise ToolError(
                    "LEASE_CONFLICT",
                    f"File is already leased by session {lease.session_id}.",
                    {"file_path": file_path, "lease": lease.model_dump(mode="json")},
                )
            cursor = conn.execute(
                """
                INSERT INTO file_leases (
                  session_id, repo_path, file_path, mode, expires_at, created_at, released_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    session_id,
                    repo_path,
                    file_path,
                    mode,
                    expires_at,
                    now,
                    json.dumps(metadata or {}),
                ),
            )
            row = conn.execute("SELECT * FROM file_leases WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._row_to_lease(row)

    def list_file_leases(
        self,
        session_id: str | None = None,
        repo_path: str | None = None,
        active_only: bool = True,
    ) -> list[FileLease]:
        clauses: list[str] = []
        args: list[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            args.append(session_id)
        if repo_path:
            clauses.append("repo_path = ?")
            args.append(repo_path)
        if active_only:
            clauses.append("released_at IS NULL")
            clauses.append("(expires_at IS NULL OR expires_at > ?)")
            args.append(utc_now_iso())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(f"SELECT * FROM file_leases{where} ORDER BY id ASC", args).fetchall()
        return [self._row_to_lease(row) for row in rows]

    def release_file_lease(
        self,
        lease_id: int | None = None,
        session_id: str | None = None,
        file_path: str | None = None,
    ) -> int:
        clauses: list[str] = ["released_at IS NULL"]
        args: list[Any] = []
        if lease_id is not None:
            clauses.append("id = ?")
            args.append(lease_id)
        if session_id:
            clauses.append("session_id = ?")
            args.append(session_id)
        if file_path:
            clauses.append("file_path = ?")
            args.append(file_path)
        if lease_id is None and not session_id:
            raise ValueError("release_file_lease requires lease_id or session_id")
        args.append(utc_now_iso())
        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE file_leases SET released_at = ? WHERE {' AND '.join(clauses)}",
                [args[-1], *args[:-1]],
            )
            return cursor.rowcount

    def _row_to_session(self, row: sqlite3.Row) -> AgentSession:
        tmux = None
        if row["tmux_session"]:
            tmux = TmuxSessionRef(
                session_name=row["tmux_session"],
                window=row["tmux_window"] or "0",
                pane=row["tmux_pane"] or "0",
            )
        return AgentSession(
            id=row["id"],
            provider_id=row["provider_id"],
            model=row["model"],
            harness=row["harness"],
            account=row["account"],
            role=row["role"],
            task=row["task"],
            repo_path=row["repo_path"],
            worktree_path=row["worktree_path"],
            runtime=row["runtime"],
            state=row["state"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            ended_at=row["ended_at"],
            tmux=tmux,
            artifact_dir=row["artifact_dir"],
            transcript_path=row["transcript_path"],
            events_path=row["events_path"],
            metadata=json.loads(row["metadata_json"]),
        )

    def _row_to_lease(self, row: sqlite3.Row) -> FileLease:
        return FileLease(
            id=row["id"],
            session_id=row["session_id"],
            repo_path=row["repo_path"],
            file_path=row["file_path"],
            mode=row["mode"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            released_at=row["released_at"],
            metadata=json.loads(row["metadata_json"]),
        )
