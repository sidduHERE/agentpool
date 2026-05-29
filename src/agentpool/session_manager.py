from __future__ import annotations

import time
import shutil
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentpool.artifacts import (
    append_transcript,
    artifact_manifest,
    collect_artifacts,
    create_artifact_dir,
    initialize_artifacts,
)
from agentpool.config import AgentPoolConfig, load_config
from agentpool.config import default_provider_config
from agentpool.event_detection import detect_event, screen_hash, trim_excerpt
from agentpool.git_worktree import cleanup_worktree, create_worktree, delete_agentpool_branch, list_agentpool_worktrees
from agentpool.models import (
    AgentSession,
    ArtifactRecord,
    FileLease,
    ObserveEvent,
    ObserveWorkerResponse,
    RuntimeKind,
    SessionState,
    SpawnWorkerRequest,
    ToolError,
)
from agentpool.policy import active_state, enforce_raw_keys_policy, enforce_spawn_policy
from agentpool.providers.registry import ProviderRegistry, build_registry
from agentpool.redaction import redact_text
from agentpool.runtimes.tmux import TmuxRuntime
from agentpool.store import Store
from agentpool.usage.summary import build_usage_summary
from agentpool.utils import append_jsonl, new_session_id, utc_now_iso, write_json

DEFAULT_SESSION_LIMIT = 50
MAX_SESSION_LIMIT = 500


class SessionManager:
    def __init__(
        self,
        config: AgentPoolConfig | None = None,
        store: Store | None = None,
        registry: ProviderRegistry | None = None,
        runtime: TmuxRuntime | None = None,
        coordinator_id: str | None = None,
        scope_sessions_by_coordinator: bool = False,
    ):
        self.config = config or load_config()
        if not self.config.providers:
            self.config.providers = default_provider_config()
        self.store = store or Store(self.config.storage.db)
        self.registry = registry or build_registry(self.config)
        self.runtime = runtime or TmuxRuntime()
        self.coordinator_id = coordinator_id or f"coord_{uuid.uuid4().hex[:12]}"
        self.scope_sessions_by_coordinator = scope_sessions_by_coordinator

    def inventory(self, include_usage: bool = True) -> dict[str, Any]:
        self.reconcile_sessions()
        return {
            "providers": [p.model_dump(mode="json") for p in self.registry.descriptors(include_usage)],
            "policy": self.config.policy.model_dump(mode="json"),
            "checked_at": utc_now_iso(),
        }

    def usage_snapshot(self, provider_id: str | None = None, backend: str = "combined") -> dict[str, Any]:
        self.reconcile_sessions()
        snapshots = self.registry.usage(provider_id, backend=backend)
        for snapshot in snapshots:
            self.store.save_usage_snapshot(snapshot)
        return {
            "snapshots": [snapshot.model_dump(mode="json") for snapshot in snapshots],
            "source": "live_probe",
            "backend": backend,
        }

    def cached_usage_snapshot(self, provider_id: str | None = None) -> dict[str, Any]:
        self.reconcile_sessions()
        snapshots = self._configured_usage_snapshots(self.store.latest_usage_snapshots(provider_id), provider_id)
        return {
            "snapshots": [snapshot.model_dump(mode="json") for snapshot in snapshots],
            "source": "sqlite_cache",
        }

    def usage_summary(self, provider_id: str | None = None, refresh: bool = False, backend: str = "combined") -> dict[str, Any]:
        self.reconcile_sessions()
        descriptors = self.registry.descriptors(include_usage=False)
        if refresh:
            snapshots = self.registry.usage(provider_id, backend=backend)
            for snapshot in snapshots:
                self.store.save_usage_snapshot(snapshot)
            source = "live_probe"
        else:
            snapshots = self._configured_usage_snapshots(self.store.latest_usage_snapshots(provider_id), provider_id)
            source = "sqlite_cache"
        return {
            **build_usage_summary(
                snapshots,
                min_remaining_percent=self.config.policy.min_remaining_percent,
                stale_after_seconds=self.config.policy.usage_stale_after_seconds,
                provider_descriptors=descriptors,
            ),
            "source": source,
            "backend": backend if refresh else "cache",
        }

    def provider_models(self, provider_id: str | None = None) -> dict[str, Any]:
        rows = []
        for descriptor in self.inventory(include_usage=False)["providers"]:
            if provider_id and descriptor["id"] != provider_id:
                continue
            metadata = descriptor.get("metadata") or {}
            rows.append(
                {
                    "provider_id": descriptor["id"],
                    "installed": descriptor["installed"],
                    "default_model": metadata.get("default_model"),
                    "smoke_model": metadata.get("smoke_model"),
                    "model_arg": metadata.get("model_arg"),
                    "model_selection": metadata.get("model_selection", "model_arg"),
                    "default_initial_prompt_mode": metadata.get("default_initial_prompt_mode", "send_after_launch"),
                    "reasoning_effort_config_key": metadata.get("reasoning_effort_config_key"),
                    "service_tier_config_key": metadata.get("service_tier_config_key"),
                    "submit_keys": metadata.get("submit_keys", []),
                    "catalog_completeness": metadata.get("catalog_completeness"),
                    "quirks": metadata.get("quirks", []),
                    "models": descriptor.get("models", []),
                }
            )
        if provider_id and not rows:
            raise ToolError(
                "PROVIDER_NOT_FOUND",
                f"Provider {provider_id} is not configured.",
                {"provider_id": provider_id},
            )
        return {"providers": rows}

    def filter_candidates(
        self,
        required_capabilities: list[str] | None = None,
        avoid_statuses: list[str] | None = None,
        allowed_providers: list[str] | None = None,
        include_usage_unknown: bool = True,
    ) -> dict[str, Any]:
        required = set(required_capabilities or [])
        avoid = set(avoid_statuses or [])
        allowed = set(allowed_providers or [])
        candidates: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for descriptor in self.registry.descriptors(include_usage=True):
            reasons: list[str] = []
            blocked: list[str] = []
            if allowed and descriptor.id not in allowed:
                blocked.append("not in allowed_providers filter")
            if not descriptor.installed:
                blocked.append("not installed")
            capabilities = {str(cap.value if hasattr(cap, "value") else cap) for cap in descriptor.capabilities}
            missing = sorted(required - capabilities)
            if missing:
                blocked.append(f"missing capabilities: {', '.join(missing)}")
            status = descriptor.usage.status if descriptor.usage else "unknown"
            status_value = status.value if hasattr(status, "value") else str(status)
            if status_value in avoid:
                blocked.append(f"usage status avoided: {status_value}")
            if status_value == "unknown" and not include_usage_unknown:
                blocked.append("usage unknown")
            if descriptor.id in self.config.policy.denied_providers:
                blocked.append("policy: provider denied")
            if blocked:
                excluded.append({"provider_id": descriptor.id, "excluded_reasons": blocked})
            else:
                reasons.extend(["installed", "supports tmux"])
                reasons.append("usage available or unknown" if include_usage_unknown else f"usage {status_value}")
                candidates.append({"provider_id": descriptor.id, "included_reasons": reasons})
        return {"candidates": candidates, "excluded": excluded}

    def spawn_worker(self, request: SpawnWorkerRequest) -> dict[str, Any]:
        self.reconcile_sessions()
        enforce_spawn_policy(self.config, request.provider_id, request.role, request.isolation)
        if request.runtime != RuntimeKind.TMUX:
            raise ToolError("POLICY_BLOCKED", "Only tmux runtime is supported in v0.1.", {"runtime": request.runtime})
        active_sessions = [
            session
            for session in self.store.list_sessions()
            if active_state(session.state) and self._session_in_scope(session)
        ]
        if len(active_sessions) >= self.config.policy.max_parallel_sessions:
            raise ToolError(
                "POLICY_BLOCKED",
                "Max parallel sessions reached.",
                {
                    "max_parallel_sessions": self.config.policy.max_parallel_sessions,
                    "active_count": len(active_sessions),
                    "active_sessions": [_session_summary(session) for session in active_sessions],
                },
            )
        self._enforce_cached_usage_policy(request.provider_id)
        adapter = self.registry.get(request.provider_id)
        descriptor = adapter.detect()
        if not descriptor.installed:
            raise ToolError(
                "PROVIDER_NOT_INSTALLED",
                f"Provider {request.provider_id} is not installed.",
                {"provider_id": request.provider_id, "warnings": descriptor.warnings},
            )
        model_was_explicit = request.model is not None
        default_model = _default_model(adapter.config.metadata)
        if not request.model and default_model:
            request = request.model_copy(update={"model": default_model})
        if model_was_explicit and not request.reasoning_effort:
            default_reasoning = _default_reasoning_effort(adapter.config.metadata, adapter.config.models, request.model)
            if default_reasoning:
                request = request.model_copy(update={"reasoning_effort": default_reasoning})
        effective_prompt_mode = _effective_initial_prompt_mode(request.initial_prompt_mode, adapter.config.metadata)
        if effective_prompt_mode != request.initial_prompt_mode:
            request = request.model_copy(update={"initial_prompt_mode": effective_prompt_mode})
        if request.max_runtime_seconds is not None and request.max_runtime_seconds <= 0:
            raise ToolError(
                "INVALID_LIMIT",
                "max_runtime_seconds must be greater than zero when provided.",
                {"max_runtime_seconds": request.max_runtime_seconds},
            )
        if request.max_turns is not None and request.max_turns <= 0:
            raise ToolError(
                "INVALID_LIMIT",
                "max_turns must be greater than zero when provided.",
                {"max_turns": request.max_turns},
            )
        repo_path = Path(request.repo_path).expanduser().resolve()
        session_id = new_session_id()
        workdir = repo_path
        worktree_path = None
        artifact_dir = None
        try:
            if request.isolation == "worktree":
                workdir = create_worktree(repo_path, request.provider_id, session_id)
                worktree_path = str(workdir)
            artifact_dir = create_artifact_dir(self.config.storage.artifacts, repo_path, session_id)
        except Exception:
            if worktree_path:
                self._rollback_spawn_files(repo_path, worktree_path, request.provider_id, session_id, artifact_dir)
            raise
        tmux_name = _tmux_name(self.config.runtime.tmux.session_prefix, request.provider_id, session_id)
        now = datetime.now(timezone.utc)
        metadata = dict(request.metadata)
        metadata["coordinator_id"] = self.coordinator_id
        metadata["initial_prompt_mode"] = request.initial_prompt_mode
        if request.reasoning_effort:
            metadata["reasoning_effort"] = request.reasoning_effort
        if request.service_tier:
            metadata["service_tier"] = request.service_tier
        if request.max_runtime_seconds:
            metadata["deadline_at"] = datetime.fromtimestamp(
                time.time() + request.max_runtime_seconds,
                tz=timezone.utc,
            ).isoformat()
        if request.max_turns:
            metadata["max_turns"] = request.max_turns
            metadata["turns_sent"] = 0
        session = AgentSession(
            id=session_id,
            provider_id=request.provider_id,
            model=request.model,
            harness=adapter.harness,
            account=request.account,
            role=request.role,
            task=request.task,
            repo_path=str(repo_path),
            worktree_path=worktree_path,
            runtime=RuntimeKind.TMUX,
            state=SessionState.STARTING,
            created_at=now,
            updated_at=now,
            artifact_dir=str(artifact_dir),
            transcript_path=str(artifact_dir / "transcript.txt"),
            events_path=str(artifact_dir / "events.jsonl"),
            metadata=metadata,
        )
        self.store.save_session(session)
        try:
            prompt = adapter.build_initial_prompt(request, session_id, workdir)
            initialize_artifacts(session, prompt)
            command = adapter.build_launch_command(request, workdir)
            if request.initial_prompt_mode == "arg":
                command = [*command, prompt]
            ref = self.runtime.spawn(command, workdir, {}, tmux_name)
        except Exception:
            self.store.update_session_state(session_id, SessionState.FAILED, ended_at=utc_now_iso())
            self._rollback_spawn_files(repo_path, worktree_path, request.provider_id, session_id, artifact_dir)
            raise
        try:
            session.tmux = ref
            session.state = SessionState.RUNNING if request.initial_prompt_mode == "arg" else SessionState.READY
            session.updated_at = datetime.now(timezone.utc)
            self.store.save_session(session)
        except Exception:
            if self.runtime.exists(ref):
                self.runtime.terminate(ref)
            self.store.update_session_state(session_id, SessionState.FAILED, ended_at=utc_now_iso())
            self._rollback_spawn_files(repo_path, worktree_path, request.provider_id, session_id, artifact_dir)
            raise
        event_command = list(command)
        if request.initial_prompt_mode == "arg" and event_command:
            event_command[-1] = "<agentpool-initial-prompt>"
        self._event(
            session,
            "spawn",
            state=session.state.value,
            metadata={"command": event_command, "initial_prompt_mode": request.initial_prompt_mode},
        )
        if request.initial_prompt_mode == "send_after_launch":
            time.sleep(0.3)
            self.runtime.send_message(ref, prompt, submit=True)
            session.state = SessionState.RUNNING
            session.updated_at = datetime.now(timezone.utc)
            session.metadata["initial_prompt_sent"] = True
            self.store.save_session(session)
            self._event(session, "send_initial_prompt", state=session.state.value)
        return {
            "session_id": session.id,
            "session": session.model_dump(mode="json"),
            "attach_command": self.runtime.attach_command(ref),
            "live_control": {
                "can_capture_screen": True,
                "can_send_message": True,
                "can_send_keys": self.config.policy.allow_raw_keys,
                "can_interrupt": True,
                "can_attach": True,
                "initial_prompt_mode": request.initial_prompt_mode,
            },
        }

    def observe_worker(
        self,
        session_id: str,
        wait_for: list[str] | None = None,
        timeout_seconds: int = 0,
        include_screen: bool = True,
        include_recent_log: bool = True,
        max_lines: int | None = None,
    ) -> ObserveWorkerResponse:
        session = self._require_session(session_id)
        timed_out = self._enforce_deadline(session)
        if timed_out:
            return timed_out
        if not session.tmux:
            raise ToolError("TMUX_SESSION_NOT_FOUND", "Session has no tmux reference.", {"session_id": session_id})
        deadline = time.monotonic() + max(0, timeout_seconds)
        wanted = set(wait_for or [])
        previous_hash = session.metadata.get("last_screen_hash")
        while True:
            timed_out = self._enforce_deadline(session)
            if timed_out:
                return timed_out
            screen = self.runtime.capture(session.tmux, max_lines or self.config.runtime.tmux.capture_lines)
            clean = redact_text(screen)
            detection = detect_event(clean, previous_hash)
            current_hash = screen_hash(clean)
            readiness = _classify_readiness(session, detection, previous_hash, current_hash, clean)
            observe_metadata = {
                "screen_hash": current_hash,
                "readiness": readiness,
                "unchanged_screen": bool(previous_hash and previous_hash == current_hash),
                "startup_warnings": _startup_warning_summary(clean),
            }
            session.state = detection.state
            session.updated_at = datetime.now(timezone.utc)
            session.metadata["last_screen_hash"] = current_hash
            self.store.save_session(session)
            append_transcript(session, clean)
            (Path(session.artifact_dir) / "latest_screen.txt").write_text(clean, encoding="utf-8")
            self._event(
                session,
                f"observe:{detection.event.value}",
                state=session.state.value,
                screen_hash=current_hash,
                excerpt=trim_excerpt(clean, 1000),
                metadata={"readiness": readiness},
            )
            event_value = detection.event.value
            if not wanted or event_value in wanted or _alias_event(event_value) in wanted or timeout_seconds <= 0:
                return ObserveWorkerResponse(
                    session_id=session_id,
                    state=session.state,
                    event=detection.event,
                    screen_excerpt=trim_excerpt(clean) if include_screen else None,
                    recent_log=trim_excerpt(clean, 2000) if include_recent_log else None,
                    parsed_question=detection.parsed_question,
                    confidence=detection.confidence,
                    metadata=observe_metadata,
                )
            if time.monotonic() >= deadline:
                return ObserveWorkerResponse(
                    session_id=session_id,
                    state=session.state,
                    event=ObserveEvent.TIMEOUT,
                    screen_excerpt=trim_excerpt(clean) if include_screen else None,
                    recent_log=trim_excerpt(clean, 2000) if include_recent_log else None,
                    confidence=detection.confidence,
                    metadata={**observe_metadata, "readiness": "timeout"},
                )
            previous_hash = current_hash
            time.sleep(0.5)

    def send_worker_message(self, session_id: str, message: str, submit: bool = True) -> dict[str, Any]:
        session = self._require_session(session_id)
        self._enforce_deadline_or_raise(session)
        self._enforce_turn_limit(session)
        if not session.tmux:
            raise ToolError("TMUX_SESSION_NOT_FOUND", "Session has no tmux reference.", {"session_id": session_id})
        submit_keys = None
        sent_empty_submit = False
        if submit and message == "":
            submit_keys = ["Enter"]
            self.runtime.send_keys(session.tmux, submit_keys)
            sent_empty_submit = True
        elif submit and not _looks_like_menu_choice(message):
            submit_keys = self.registry.get(session.provider_id).submit_keys()
        if submit_keys and not sent_empty_submit:
            self.runtime.send_message(session.tmux, message, submit=False)
            time.sleep(0.1)
            self.runtime.send_keys(session.tmux, submit_keys)
        elif not sent_empty_submit and (message != "" or not submit):
            self.runtime.send_message(session.tmux, message, submit=submit)
        event_id = self._event(
            session,
            "send_message",
            state=session.state.value,
            metadata={"submit": submit, "submit_keys": submit_keys},
        )
        session.metadata["turns_sent"] = int(session.metadata.get("turns_sent") or 0) + 1
        session.updated_at = datetime.now(timezone.utc)
        self.store.save_session(session)
        append_transcript(session, f"\n[agentpool sent]\n{redact_text(message)}\n")
        return {"ok": True, "session_id": session_id, "event_id": event_id}

    def send_worker_keys(self, session_id: str, keys: list[str]) -> dict[str, Any]:
        enforce_raw_keys_policy(self.config, keys)
        session = self._require_session(session_id)
        self._enforce_deadline_or_raise(session)
        if not session.tmux:
            raise ToolError("TMUX_SESSION_NOT_FOUND", "Session has no tmux reference.", {"session_id": session_id})
        self.runtime.send_keys(session.tmux, keys)
        self._event(session, "send_keys", state=session.state.value, metadata={"keys": keys})
        return {"ok": True, "session_id": session_id}

    def interrupt_worker(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        if not session.tmux:
            raise ToolError("TMUX_SESSION_NOT_FOUND", "Session has no tmux reference.", {"session_id": session_id})
        self.runtime.interrupt(session.tmux)
        self._event(session, "interrupt", state=session.state.value)
        return {"ok": True, "state": session.state.value if hasattr(session.state, "value") else session.state}

    def attach_info(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        if not session.tmux:
            raise ToolError("TMUX_SESSION_NOT_FOUND", "Session has no tmux reference.", {"session_id": session_id})
        return {
            "session_id": session_id,
            "attach_command": self.runtime.attach_command(session.tmux),
            "tmux_session": session.tmux.session_name,
            "pane_target": session.tmux.target,
        }

    def collect_worker_artifacts(
        self,
        session_id: str,
        include_diff: bool = True,
        include_transcript: bool = True,
        mark_completed: bool = False,
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        screen = ""
        if session.tmux and self.runtime.exists(session.tmux):
            try:
                screen = self.runtime.capture(session.tmux, self.config.runtime.tmux.capture_lines)
            except ToolError as exc:
                if exc.error.code != "TMUX_SESSION_NOT_FOUND":
                    raise
                latest_screen = Path(session.artifact_dir) / "latest_screen.txt"
                screen = latest_screen.read_text(encoding="utf-8") if latest_screen.exists() else ""
        screen = redact_text(screen)
        result = collect_artifacts(session, screen, include_diff=include_diff)
        for artifact in result["artifacts"]:
            self.store.save_artifact(session_id, artifact=ArtifactRecord.model_validate(artifact))
        if mark_completed and session.state not in {SessionState.CANCELLED, SessionState.FAILED}:
            session.state = SessionState.COMPLETED
            self.store.update_session_state(session_id, SessionState.COMPLETED, ended_at=utc_now_iso())
            result["state"] = SessionState.COMPLETED.value
        self._event(session, "collect", state=result["state"], metadata={"include_diff": include_diff})
        if not include_transcript:
            result["artifacts"] = [a for a in result["artifacts"] if a["kind"] != "transcript"]
        return result

    def artifact_manifest(self, session_id: str) -> dict[str, Any]:
        return artifact_manifest(self._require_session(session_id))

    def read_transcript(
        self,
        session_id: str,
        offset: int = 0,
        limit: int = 4000,
        tail_lines: int | None = None,
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        if offset < 0:
            raise ToolError("INVALID_TRANSCRIPT_RANGE", "offset must be zero or greater.", {"offset": offset})
        if limit <= 0 or limit > 200_000:
            raise ToolError(
                "INVALID_TRANSCRIPT_RANGE",
                "limit must be between 1 and 200000 bytes.",
                {"limit": limit},
            )
        if tail_lines is not None and (tail_lines <= 0 or tail_lines > 10_000):
            raise ToolError(
                "INVALID_TRANSCRIPT_RANGE",
                "tail_lines must be between 1 and 10000.",
                {"tail_lines": tail_lines},
            )
        if tail_lines is not None and offset:
            raise ToolError(
                "INVALID_TRANSCRIPT_RANGE",
                "Use either offset pagination or tail_lines, not both.",
                {"offset": offset, "tail_lines": tail_lines},
            )
        path = Path(session.transcript_path)
        if not path.exists():
            return _transcript_payload(session, path, "", offset=0, next_offset=0, size_bytes=0, mode="missing")
        size_bytes = path.stat().st_size
        if tail_lines is not None:
            lines = deque(maxlen=tail_lines)
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                lines.extend(fh)
            text = "".join(lines)
            return _transcript_payload(
                session,
                path,
                text,
                offset=None,
                next_offset=None,
                size_bytes=size_bytes,
                mode="tail",
                tail_lines=tail_lines,
                has_more=len(lines) == tail_lines and bool(size_bytes),
            )
        start = min(offset, size_bytes)
        with path.open("rb") as fh:
            fh.seek(start)
            raw = fh.read(limit)
        next_offset = start + len(raw)
        return _transcript_payload(
            session,
            path,
            raw.decode("utf-8", errors="replace"),
            offset=start,
            next_offset=next_offset,
            size_bytes=size_bytes,
            mode="page",
            limit=limit,
            has_more=next_offset < size_bytes,
        )

    def acquire_file_lease(
        self,
        session_id: str,
        file_path: str,
        mode: str = "write",
        ttl_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        if mode not in {"read", "write"}:
            raise ToolError("INVALID_LEASE_MODE", "Lease mode must be read or write.", {"mode": mode})
        expires_at = None
        if ttl_seconds:
            expires_at = datetime.fromtimestamp(time.time() + ttl_seconds, tz=timezone.utc).isoformat()
        normalized = _normalize_file_path(session, file_path)
        lease = self.store.acquire_file_lease(
            session_id=session_id,
            repo_path=session.repo_path,
            file_path=normalized,
            mode=mode,
            expires_at=expires_at,
            metadata=metadata,
        )
        if lease.session_id != session_id:
            raise ToolError(
                "LEASE_CONFLICT",
                f"File is already leased by session {lease.session_id}.",
                {"file_path": normalized, "lease": lease.model_dump(mode="json")},
            )
        self._event(session, "lease_acquire", state=session.state.value, metadata={"file_path": normalized, "mode": mode})
        return {"ok": True, "lease": lease.model_dump(mode="json")}

    def list_file_leases(
        self,
        session_id: str | None = None,
        repo_path: str | None = None,
        active_only: bool = True,
    ) -> dict[str, Any]:
        normalized_repo = str(Path(repo_path).expanduser().resolve()) if repo_path else None
        leases = self.store.list_file_leases(session_id=session_id, repo_path=normalized_repo, active_only=active_only)
        return {"leases": [lease.model_dump(mode="json") for lease in leases]}

    def release_file_lease(
        self,
        lease_id: int | None = None,
        session_id: str | None = None,
        file_path: str | None = None,
    ) -> dict[str, Any]:
        normalized = file_path
        if session_id and file_path:
            normalized = _normalize_file_path(self._require_session(session_id), file_path)
        released = self.store.release_file_lease(lease_id=lease_id, session_id=session_id, file_path=normalized)
        if session_id:
            session = self._require_session(session_id)
            self._event(session, "lease_release", state=session.state.value, metadata={"lease_id": lease_id, "file_path": normalized})
        return {"ok": True, "released": released}

    def list_worktrees(self, repo_path: str) -> dict[str, Any]:
        repo = Path(repo_path).expanduser().resolve()
        active_worktrees = {
            str(session.worktree_path)
            for session in self.store.list_sessions()
            if session.worktree_path and active_state(session.state)
        }
        worktrees = []
        for entry in list_agentpool_worktrees(repo):
            path = str(entry.get("path") or "")
            worktrees.append({**entry, "active": path in active_worktrees})
        return {"repo_path": str(repo), "worktrees": worktrees}

    def cleanup_worktree(self, session_id: str, force: bool = False) -> dict[str, Any]:
        session = self._require_session(session_id)
        if not session.worktree_path:
            return {"removed": False, "reason": "session has no worktree", "session_id": session_id}
        if active_state(session.state) and not force:
            raise ToolError(
                "WORKTREE_ACTIVE",
                "Refusing to remove an active session worktree; terminate first or pass force.",
                {"session_id": session_id, "state": session.state.value if hasattr(session.state, "value") else session.state},
            )
        result = cleanup_worktree(Path(session.repo_path), Path(session.worktree_path), force=force)
        self._event(session, "worktree_cleanup", state=session.state.value, metadata={"force": force, **result})
        return {"session_id": session_id, **result}

    def list_sessions(
        self,
        states: list[str] | str | None = None,
        provider_id: str | None = None,
        include_all: bool = False,
        limit: int | None = DEFAULT_SESSION_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        self.reconcile_sessions()
        limit = _normalize_session_limit(limit)
        if offset < 0:
            raise ToolError("INVALID_SESSION_PAGE", "offset must be zero or greater.", {"offset": offset})
        normalized_states = _normalize_state_filter(states)
        if self.scope_sessions_by_coordinator and not include_all:
            all_sessions = [
                session
                for session in self.store.list_sessions(normalized_states, provider_id)
                if self._session_in_scope(session)
            ]
            total = len(all_sessions)
            sessions = all_sessions[offset:] if limit is None else all_sessions[offset : offset + limit]
        else:
            total = self.store.count_sessions(normalized_states, provider_id)
            sessions = self.store.list_sessions(normalized_states, provider_id, limit=limit, offset=offset)
        next_offset = offset + len(sessions)
        has_more = next_offset < total
        return {
            "sessions": [session.model_dump(mode="json") for session in sessions],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "count": len(sessions),
                "total": total,
                "has_more": has_more,
                "next_offset": next_offset if has_more else None,
            },
            "scope": {
                "coordinator_id": self.coordinator_id,
                "current_coordinator_only": self.scope_sessions_by_coordinator and not include_all,
            },
        }

    def get_session(self, session_id: str) -> dict[str, Any]:
        return {"session": self._require_session(session_id).model_dump(mode="json")}

    def terminate_worker(self, session_id: str, reason: str | None = None) -> dict[str, Any]:
        session = self._require_session(session_id)
        if session.tmux and self.runtime.exists(session.tmux):
            self.runtime.terminate(session.tmux)
        state = session.state if isinstance(session.state, SessionState) else SessionState(session.state)
        final_state = state if not active_state(state) else SessionState.CANCELLED
        self.store.update_session_state(session_id, final_state, ended_at=utc_now_iso())
        self._event(session, "terminate", state=final_state.value, metadata={"reason": reason})
        return {"session_id": session_id, "ok": True, "state": final_state.value}

    def reconcile_sessions(self) -> dict[str, Any]:
        reconciled = []
        for session in self.store.list_sessions():
            if not active_state(session.state) or not session.tmux:
                continue
            timed_out = self._enforce_deadline(session)
            if timed_out:
                reconciled.append(session.id)
                continue
            if self.runtime.exists(session.tmux):
                continue
            self.store.update_session_state(session.id, SessionState.FAILED, ended_at=utc_now_iso())
            self._event(
                session,
                "reconcile_dead_tmux",
                state=SessionState.FAILED.value,
                metadata={"tmux_session": session.tmux.session_name},
            )
            reconciled.append(session.id)
        return {"reconciled": reconciled, "count": len(reconciled)}

    def _require_session(self, session_id: str) -> AgentSession:
        session = self.store.get_session(session_id)
        if not session:
            raise ToolError("TMUX_SESSION_NOT_FOUND", f"Session {session_id} was not found.", {"session_id": session_id})
        return session

    def _session_in_scope(self, session: AgentSession) -> bool:
        if not self.scope_sessions_by_coordinator:
            return True
        return (session.metadata or {}).get("coordinator_id") == self.coordinator_id

    def _event(
        self,
        session: AgentSession,
        event_type: str,
        state: str | None = None,
        screen_hash: str | None = None,
        excerpt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        event_id = self.store.append_event(
            session.id,
            event_type,
            state=state,
            screen_hash=screen_hash,
            excerpt=excerpt,
            metadata=metadata,
        )
        append_jsonl(
            Path(session.events_path),
            {
                "event_id": event_id,
                "ts": utc_now_iso(),
                "event_type": event_type,
                "state": state,
                "screen_hash": screen_hash,
                "excerpt": excerpt,
                "metadata": metadata or {},
            },
        )
        return event_id

    def _enforce_cached_usage_policy(self, provider_id: str) -> None:
        snapshots = self.store.latest_usage_snapshots(provider_id)
        if not snapshots:
            return
        snapshot = snapshots[0]
        blocked = set(self.config.policy.block_on_usage_statuses)
        status = snapshot.status.value if hasattr(snapshot.status, "value") else str(snapshot.status)
        if status not in blocked:
            return
        raise ToolError(
            "USAGE_POLICY_BLOCKED",
            f"Provider {provider_id} is blocked by cached usage status: {status}.",
            {
                "provider_id": provider_id,
                "status": status,
                "policy": "block_on_usage_statuses",
                "source": "sqlite_cache",
                "checked_at": snapshot.checked_at.isoformat(),
            },
        )

    def _configured_usage_snapshots(
        self,
        snapshots: list[Any],
        provider_id: str | None = None,
    ) -> list[Any]:
        if provider_id:
            return snapshots
        configured = set(self.config.providers)
        return [snapshot for snapshot in snapshots if snapshot.provider_id in configured]

    def _enforce_deadline(self, session: AgentSession) -> ObserveWorkerResponse | None:
        deadline_at = session.metadata.get("deadline_at")
        if not deadline_at or not active_state(session.state):
            return None
        try:
            deadline = datetime.fromisoformat(str(deadline_at))
        except ValueError:
            return None
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) <= deadline:
            return None
        if session.tmux and self.runtime.exists(session.tmux):
            self.runtime.terminate(session.tmux)
        session.state = SessionState.CANCELLED
        session.ended_at = datetime.now(timezone.utc)
        self.store.update_session_state(session.id, SessionState.CANCELLED, ended_at=session.ended_at.isoformat())
        self._event(session, "timeout", state=SessionState.CANCELLED.value, metadata={"deadline_at": str(deadline_at)})
        return ObserveWorkerResponse(
            session_id=session.id,
            state=SessionState.CANCELLED,
            event=ObserveEvent.TIMEOUT,
            confidence="observed",
            metadata={"deadline_at": str(deadline_at)},
        )

    def _enforce_deadline_or_raise(self, session: AgentSession) -> None:
        timed_out = self._enforce_deadline(session)
        if timed_out:
            raise ToolError(
                "SESSION_TIMEOUT",
                "Session exceeded max_runtime_seconds and was terminated.",
                {"session_id": session.id, **timed_out.metadata},
            )

    def _enforce_turn_limit(self, session: AgentSession) -> None:
        max_turns = session.metadata.get("max_turns")
        if max_turns is None:
            return
        turns_sent = int(session.metadata.get("turns_sent") or 0)
        if turns_sent < int(max_turns):
            return
        raise ToolError(
            "TURN_LIMIT_REACHED",
            "Session reached max_turns.",
            {"session_id": session.id, "max_turns": max_turns, "turns_sent": turns_sent},
        )

    def _rollback_spawn_files(
        self,
        repo_path: Path,
        worktree_path: str | None,
        provider_id: str,
        session_id: str,
        artifact_dir: Path | None,
    ) -> None:
        if worktree_path:
            try:
                cleanup_worktree(repo_path, Path(worktree_path), force=True)
            except ToolError:
                pass
            delete_agentpool_branch(repo_path, provider_id, session_id)
        if artifact_dir and artifact_dir.exists():
            shutil.rmtree(artifact_dir, ignore_errors=True)


def _tmux_name(prefix: str, provider_id: str, session_id: str) -> str:
    safe_provider = "".join(ch if ch.isalnum() else "-" for ch in provider_id).strip("-")
    return f"{prefix}-{safe_provider}-{session_id[-6:]}"


def _default_model(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("default_model")
    return str(value) if value else None


def _normalize_state_filter(states: list[str] | str | None) -> list[str] | None:
    if states is None:
        return None
    raw_states = [states] if isinstance(states, str) else list(states)
    normalized: list[str] = []
    for state in raw_states:
        value = str(state)
        try:
            normalized.append(SessionState(value).value)
            continue
        except ValueError:
            pass
        upper = value.upper()
        try:
            normalized.append(SessionState(upper).value)
        except ValueError:
            normalized.append(value)
    return normalized


def _normalize_session_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    if limit <= 0 or limit > MAX_SESSION_LIMIT:
        raise ToolError(
            "INVALID_SESSION_PAGE",
            f"limit must be between 1 and {MAX_SESSION_LIMIT}.",
            {"limit": limit, "max_limit": MAX_SESSION_LIMIT},
        )
    return limit


def _session_summary(session: AgentSession) -> dict[str, Any]:
    metadata = session.metadata or {}
    return {
        "id": session.id,
        "provider_id": session.provider_id,
        "state": session.state.value if hasattr(session.state, "value") else session.state,
        "created_at": session.created_at.isoformat(),
        "deadline_at": metadata.get("deadline_at"),
        "tmux_session": session.tmux.session_name if session.tmux else None,
    }


def _default_reasoning_effort(
    provider_metadata: dict[str, Any],
    models: list[dict[str, Any]],
    model: str | None,
) -> str | None:
    if not model or not provider_metadata.get("reasoning_effort_config_key"):
        return None
    for entry in models:
        if not isinstance(entry, dict) or entry.get("id") != model:
            continue
        model_metadata = entry.get("metadata") or {}
        reasoning = model_metadata.get("reasoning") if isinstance(model_metadata, dict) else None
        if isinstance(reasoning, dict) and reasoning.get("default"):
            return str(reasoning["default"])
    return None


def _effective_initial_prompt_mode(requested: str, metadata: dict[str, Any]) -> str:
    if requested != "provider_default":
        return requested
    configured = metadata.get("default_initial_prompt_mode")
    if configured in {"send_after_launch", "arg", "stdin"}:
        return str(configured)
    return "send_after_launch"


def _alias_event(event: str) -> str:
    return {"approval_prompt": "approval"}.get(event, event)


def _looks_like_menu_choice(message: str) -> bool:
    stripped = message.strip()
    return bool(stripped and len(stripped) <= 3 and stripped.isalnum())


def _classify_readiness(
    session: AgentSession,
    detection: Any,
    previous_hash: str | None,
    current_hash: str,
    screen: str,
) -> str:
    if detection.event == ObserveEvent.COMPLETED:
        return "completed"
    if detection.event == ObserveEvent.ERROR:
        return "failed"
    if detection.event == ObserveEvent.QUESTION:
        return "waiting_on_question"
    if detection.event == ObserveEvent.OVERAGE_PROMPT:
        return "waiting_on_overage_prompt"
    if detection.event == ObserveEvent.APPROVAL_PROMPT:
        return "waiting_on_startup_prompt" if _looks_like_startup_prompt(screen) else "waiting_on_approval"
    if detection.event == ObserveEvent.LIMIT_WARNING:
        return "running_limit_warning"
    if previous_hash and previous_hash == current_hash:
        return "stuck_unchanged_screen"
    if session.state == SessionState.READY and session.metadata.get("initial_prompt_mode") == "send_after_launch":
        return "pasted_but_not_submitted"
    return "running"


def _looks_like_startup_prompt(screen: str) -> bool:
    lowered = screen.lower()
    return any(
        phrase in lowered
        for phrase in [
            "update available",
            "do you trust the contents of this directory",
            "hooks need review",
            "mcp startup",
            "mcp client for",
        ]
    )


def _startup_warning_summary(screen: str) -> list[str]:
    warnings = []
    lowered = screen.lower()
    if "update available" in lowered:
        warnings.append("update_available")
    if "mcp client for" in lowered or "mcp startup" in lowered:
        warnings.append("mcp_startup_warning")
    if "do you trust the contents of this directory" in lowered:
        warnings.append("directory_trust_prompt")
    if "hooks need review" in lowered:
        warnings.append("hooks_need_review")
    return warnings


def _normalize_file_path(session: AgentSession, file_path: str) -> str:
    raw = Path(file_path).expanduser()
    if not raw.is_absolute():
        return raw.as_posix()
    for base in [session.worktree_path, session.repo_path]:
        if not base:
            continue
        try:
            return raw.resolve().relative_to(Path(base).expanduser().resolve()).as_posix()
        except ValueError:
            continue
    return raw.as_posix()


def _transcript_payload(
    session: AgentSession,
    path: Path,
    text: str,
    *,
    offset: int | None,
    next_offset: int | None,
    size_bytes: int,
    mode: str,
    limit: int | None = None,
    tail_lines: int | None = None,
    has_more: bool = False,
) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "path": str(path),
        "exists": path.exists(),
        "mode": mode,
        "offset": offset,
        "limit": limit,
        "tail_lines": tail_lines,
        "next_offset": next_offset,
        "has_more": has_more,
        "size_bytes": size_bytes,
        "text": text,
    }


def manager_from_config(path: Path | None = None) -> SessionManager:
    config = load_config(path)
    return SessionManager(config=config)


def write_default_config(path: Path) -> None:
    config = load_config(Path("__missing_agentpool_config__.yaml"))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, config.model_dump(mode="json"))
