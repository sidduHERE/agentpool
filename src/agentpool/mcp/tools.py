from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agentpool.agent_io import (
    collect_payload,
    compact_artifact_manifest,
    lockdown_resource,
    observe_payload,
    parse_detail,
)
from agentpool.config import DEFAULT_MODEL_CATALOG_PATH, validate_model_catalog_path
from agentpool.models import SpawnWorkerRequest, ToolError
from agentpool.session_manager import SessionManager
from agentpool.stats.card import render_stats_card
from agentpool.stats.compute import compute_stats, filter_sections
from agentpool.stats.window import parse_window


def structured_error(exc: ToolError) -> dict[str, Any]:
    return {"error": exc.error.model_dump(mode="json")}


def _jsonable_validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    errors = exc.errors(include_url=False)
    for error in errors:
        ctx = error.get("ctx")
        if isinstance(ctx, dict):
            error["ctx"] = {key: str(value) for key, value in ctx.items()}
    return errors


def get_inventory(manager: SessionManager, include_usage: bool = True) -> dict[str, Any]:
    return manager.inventory(include_usage=include_usage)


def get_usage_snapshot(
    manager: SessionManager,
    provider_id: str | None = None,
    refresh: bool = True,
    backend: str = "combined",
) -> dict[str, Any]:
    if refresh:
        return manager.usage_snapshot(provider_id, backend=backend)
    return manager.cached_usage_snapshot(provider_id)


def get_usage_summary(
    manager: SessionManager,
    provider_id: str | None = None,
    refresh: bool = False,
    backend: str = "combined",
) -> dict[str, Any]:
    return manager.usage_summary(provider_id=provider_id, refresh=refresh, backend=backend)


def get_provider_models(manager: SessionManager, provider_id: str | None = None) -> dict[str, Any]:
    return manager.provider_models(provider_id)


def validate_model_catalog(manager: SessionManager, path: str | None = None) -> dict[str, Any]:
    return validate_model_catalog_path(
        Path(path).expanduser() if path else DEFAULT_MODEL_CATALOG_PATH,
        known_provider_ids=set(manager.config.providers),
    )


def filter_candidates(
    manager: SessionManager,
    required_capabilities: list[str] | None = None,
    avoid_statuses: list[str] | None = None,
    allowed_providers: list[str] | None = None,
    include_usage_unknown: bool = True,
) -> dict[str, Any]:
    return manager.filter_candidates(
        required_capabilities=required_capabilities,
        avoid_statuses=avoid_statuses,
        allowed_providers=allowed_providers,
        include_usage_unknown=include_usage_unknown,
    )


def spawn_worker(manager: SessionManager, **kwargs: Any) -> dict[str, Any]:
    try:
        request = SpawnWorkerRequest.model_validate(kwargs)
    except ValidationError as exc:
        raise ToolError(
            "INVALID_REQUEST",
            "Invalid spawn_worker request.",
            {"errors": _jsonable_validation_errors(exc)},
        ) from exc
    return manager.spawn_worker(request)


def observe_worker(
    manager: SessionManager,
    session_id: str,
    wait_for: list[str] | None = None,
    timeout_seconds: int = 0,
    detail: str = "summary",
    max_lines: int | None = None,
    lockdown: bool = False,
) -> dict[str, Any]:
    parsed_detail = parse_detail(detail)
    response = manager.observe_worker(
        session_id,
        wait_for=wait_for,
        timeout_seconds=timeout_seconds,
        include_screen=parsed_detail != "summary" and not lockdown,
        include_recent_log=False,
        max_lines=max_lines,
    )
    return observe_payload(response.model_dump(mode="json"), manager.artifact_manifest(session_id), parsed_detail, lockdown)


def send_worker_message(
    manager: SessionManager, session_id: str, message: str, submit: bool = True
) -> dict[str, Any]:
    return manager.send_worker_message(session_id, message, submit)


def send_worker_keys(manager: SessionManager, session_id: str, keys: list[str]) -> dict[str, Any]:
    return manager.send_worker_keys(session_id, keys)


def interrupt_worker(manager: SessionManager, session_id: str) -> dict[str, Any]:
    return manager.interrupt_worker(session_id)


def attach_info(manager: SessionManager, session_id: str) -> dict[str, Any]:
    return manager.attach_info(session_id)


def collect_worker_artifacts(
    manager: SessionManager,
    session_id: str,
    include_diff: bool = True,
    include_transcript: bool = True,
    mark_completed: bool = False,
    detail: str = "summary",
    lockdown: bool = False,
) -> dict[str, Any]:
    parsed_detail = parse_detail(detail)
    result = manager.collect_worker_artifacts(session_id, include_diff, include_transcript, mark_completed)
    return collect_payload(result, parsed_detail, lockdown)


def get_artifact_manifest(
    manager: SessionManager,
    session_id: str,
    lockdown: bool = False,
) -> dict[str, Any]:
    return compact_artifact_manifest(manager.artifact_manifest(session_id), lockdown=lockdown)


def read_worker_transcript(
    manager: SessionManager,
    session_id: str,
    offset: int = 0,
    limit: int = 4000,
    tail_lines: int | None = None,
    lockdown: bool = False,
) -> dict[str, Any]:
    if lockdown:
        session = manager._require_session(session_id)
        return {"session_id": session_id, **lockdown_resource(session.transcript_path, "transcript")}
    return manager.read_transcript(session_id, offset=offset, limit=limit, tail_lines=tail_lines)


def acquire_file_lease(
    manager: SessionManager,
    session_id: str,
    file_path: str,
    mode: str = "write",
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    return manager.acquire_file_lease(session_id, file_path, mode=mode, ttl_seconds=ttl_seconds)


def list_file_leases(
    manager: SessionManager,
    session_id: str | None = None,
    repo_path: str | None = None,
    active_only: bool = True,
) -> dict[str, Any]:
    return manager.list_file_leases(session_id=session_id, repo_path=repo_path, active_only=active_only)


def release_file_lease(
    manager: SessionManager,
    lease_id: int | None = None,
    session_id: str | None = None,
    file_path: str | None = None,
) -> dict[str, Any]:
    try:
        return manager.release_file_lease(lease_id=lease_id, session_id=session_id, file_path=file_path)
    except ValueError as exc:
        raise ToolError("INVALID_LEASE_RELEASE", str(exc)) from exc


def list_worktrees(manager: SessionManager, repo_path: str) -> dict[str, Any]:
    return manager.list_worktrees(repo_path)


def cleanup_worktree(manager: SessionManager, session_id: str, force: bool = False) -> dict[str, Any]:
    return manager.cleanup_worktree(session_id, force=force)


def list_sessions(
    manager: SessionManager,
    state: list[str] | str | None = None,
    provider_id: str | None = None,
    include_all: bool = False,
    limit: int | None = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return manager.list_sessions(state, provider_id, include_all=include_all, limit=limit, offset=offset)


def get_stats(
    manager: SessionManager,
    window: str = "7d",
    provider_id: str | None = None,
    sections: list[str] | None = None,
    scope: str = "mine",
) -> dict[str, Any]:
    manager.reconcile_sessions()
    parsed = parse_window(window)
    stats = compute_stats(
        store=manager.store,
        config=manager.config,
        registry=manager.registry,
        window=parsed,
        provider_id=provider_id,
        scope=scope,
        coordinator_id=manager.coordinator_id,
    )
    return filter_sections(stats, sections)


def get_stats_card(
    manager: SessionManager,
    window: str = "7d",
    output_path: str | None = None,
    scope: str = "mine",
) -> dict[str, Any]:
    stats = get_stats(manager, window=window, scope=scope)
    return render_stats_card(stats, output_path)


def get_session(manager: SessionManager, session_id: str) -> dict[str, Any]:
    return manager.get_session(session_id)


def terminate_worker(manager: SessionManager, session_id: str, reason: str | None = None) -> dict[str, Any]:
    return manager.terminate_worker(session_id, reason)
