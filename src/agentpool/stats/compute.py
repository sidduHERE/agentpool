from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agentpool.config import AgentPoolConfig
from agentpool.models import AgentSession, CapacitySnapshot, ProviderDescriptor
from agentpool.providers.registry import ProviderRegistry
from agentpool.stats.queries import (
    has_any_usage_snapshots,
    list_events_in_window,
    list_sessions_in_window,
    usage_snapshot_at_or_before,
    usage_snapshots_in_window,
)
from agentpool.stats.window import Window
from agentpool.store import Store
from agentpool.usage.summary import _usable_reason
from agentpool.utils import utc_now_iso

STATS_SCHEMA_VERSION = "stats/v1"
WALLS_DEFINITION = "see docs/stats.md#walls"
CORE_KEYS = frozenset({"schema_version", "generated_at", "source", "scope", "window", "filters", "data_quality"})
SECTION_KEYS = frozenset(
    {
        "sessions",
        "parallelism",
        "walls",
        "quota",
        "utilization",
        "tokens",
        "suggested_next",
        "coordinator_id",
    }
)
QUOTA_REASON_PREFIXES = ("limit_reached", "near_limit")
TOKEN_CAPABLE_PROVIDERS = {"claude-code"}


def compute_stats(
    store: Store,
    config: AgentPoolConfig,
    registry: ProviderRegistry,
    window: Window,
    *,
    provider_id: str | None = None,
    scope: str = "all",
    coordinator_id: str | None = None,
) -> dict[str, Any]:
    scope_normalized = scope if scope in {"mine", "all"} else "all"
    scope_mine = scope_normalized == "mine"
    descriptors = registry.descriptors(include_usage=False)
    descriptor_by_id = {descriptor.id: descriptor for descriptor in descriptors}
    configured_provider_ids = sorted(config.providers.keys())

    sessions = list_sessions_in_window(
        store,
        window,
        provider_id=provider_id,
        coordinator_id=coordinator_id,
        scope_mine=scope_mine,
    )
    session_ids = {session.id for session in sessions}
    events = list_events_in_window(store, window, session_ids=session_ids or None)

    data_quality: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "schema_version": STATS_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source": "computed",
        "scope": scope_normalized,
        "window": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "label": window.label,
            "spec": window.spec,
        },
        "filters": {"provider_id": provider_id},
        "data_quality": data_quality,
    }
    if scope_mine and coordinator_id:
        result["coordinator_id"] = coordinator_id

    result["sessions"] = _compute_sessions(sessions, events)
    result["parallelism"] = _compute_parallelism(sessions, window.end)
    result["walls"] = _compute_walls(
        store=store,
        events=events,
        configured_provider_ids=configured_provider_ids,
        descriptor_by_id=descriptor_by_id,
        min_remaining_percent=config.policy.min_remaining_percent,
        stale_after_seconds=config.policy.usage_stale_after_seconds,
        data_quality=data_quality,
    )
    result["quota"] = _compute_quota(
        store,
        window,
        configured_provider_ids,
        provider_id,
        data_quality,
    )
    result["utilization"] = _compute_utilization(
        sessions=sessions,
        window=window,
        quota=result["quota"],
        sum_worker_hours=result["parallelism"]["sum_worker_hours"],
    )
    result["tokens"] = _compute_tokens(
        store,
        configured_provider_ids,
        descriptor_by_id,
        data_quality,
    )
    result["suggested_next"] = _suggested_next(window, result)
    return result


def filter_sections(stats: dict[str, Any], sections: list[str] | None) -> dict[str, Any]:
    if not sections:
        return stats
    allowed = {section.strip() for section in sections if section.strip()}
    filtered: dict[str, Any] = {}
    for key in CORE_KEYS:
        if key in stats:
            filtered[key] = stats[key]
    if "coordinator_id" in stats:
        filtered["coordinator_id"] = stats["coordinator_id"]
    for key in allowed:
        if key in SECTION_KEYS and key in stats:
            filtered[key] = stats[key]
    return filtered


def _compute_sessions(sessions: list[AgentSession], events: list[dict[str, Any]]) -> dict[str, Any]:
    by_provider: dict[str, int] = {}
    by_role: dict[str, int] = {}
    by_state: dict[str, int] = {}
    for session in sessions:
        by_provider[session.provider_id] = by_provider.get(session.provider_id, 0) + 1
        by_role[session.role] = by_role.get(session.role, 0) + 1
        state = session.state.value if hasattr(session.state, "value") else str(session.state)
        by_state[state] = by_state.get(state, 0) + 1

    spawned = sum(1 for event in events if event["event_type"] == "spawn")
    terminated = sum(1 for event in events if event["event_type"] == "terminate")
    interrupted = sum(1 for event in events if event["event_type"] == "interrupt")
    timed_out = sum(1 for event in events if event["event_type"] == "timeout")

    return {
        "total": len(sessions),
        "by_provider": dict(sorted(by_provider.items())),
        "by_role": dict(sorted(by_role.items())),
        "by_state": dict(sorted(by_state.items())),
        "spawned": spawned,
        "terminated": terminated,
        "interrupted": interrupted,
        "timed_out": timed_out,
    }


def _compute_parallelism(sessions: list[AgentSession], window_end: datetime) -> dict[str, Any]:
    if not sessions:
        return {
            "wall_clock_hours": 0.0,
            "sum_worker_hours": 0.0,
            "ratio": None,
            "peak_concurrent": 0,
            "peak_at": None,
        }

    intervals: list[tuple[datetime, datetime]] = []
    for session in sessions:
        start = _ensure_utc(session.created_at)
        end = _ensure_utc(session.ended_at) if session.ended_at else window_end
        if end < start:
            end = start
        intervals.append((start, end))

    earliest = min(start for start, _ in intervals)
    latest = max(end for _, end in intervals)
    wall_clock_hours = max(0.0, (latest - earliest).total_seconds() / 3600.0)
    sum_worker_hours = sum((end - start).total_seconds() / 3600.0 for start, end in intervals)
    ratio = round(sum_worker_hours / wall_clock_hours, 2) if wall_clock_hours > 0 else None

    timeline: list[tuple[datetime, int]] = []
    for start, end in intervals:
        timeline.append((start, 1))
        timeline.append((end, -1))
    timeline.sort(key=lambda item: (item[0], -item[1]))

    running = 0
    peak = 0
    peak_at: datetime | None = None
    for ts, delta in timeline:
        running += delta
        if running > peak:
            peak = running
            peak_at = ts

    return {
        "wall_clock_hours": round(wall_clock_hours, 2),
        "sum_worker_hours": round(sum_worker_hours, 2),
        "ratio": ratio,
        "peak_concurrent": peak,
        "peak_at": peak_at.isoformat() if peak_at else None,
    }


def _compute_walls(
    *,
    store: Store,
    events: list[dict[str, Any]],
    configured_provider_ids: list[str],
    descriptor_by_id: dict[str, ProviderDescriptor],
    min_remaining_percent: int,
    stale_after_seconds: int,
    data_quality: list[dict[str, Any]],
) -> dict[str, Any]:
    spawn_events = [event for event in events if event["event_type"] == "spawn"]
    snapshot_max_age = 2 * stale_after_seconds
    total_snapshots = has_any_usage_snapshots(store)

    if not spawn_events:
        walls = {
            "hit": 0 if total_snapshots else None,
            "avoided": 0 if total_snapshots else None,
            "by_provider": {},
            "confidence": "high" if total_snapshots else "low",
            "definition": WALLS_DEFINITION,
        }
        if not total_snapshots:
            data_quality.append(
                {
                    "code": "no_usage_data_in_window",
                    "impact": "walls undercount",
                    "note": "No usage snapshots available for wall inference.",
                }
            )
        return walls

    if not total_snapshots:
        data_quality.append(
            {
                "code": "no_usage_data_in_window",
                "impact": "walls undercount",
                "note": "No usage snapshots available for wall inference.",
            }
        )
        return {
            "hit": None,
            "avoided": None,
            "by_provider": {},
            "confidence": "low",
            "definition": WALLS_DEFINITION,
        }

    hit = 0
    avoided = 0
    by_provider: dict[str, dict[str, int]] = {}
    unknown_spawns = 0

    for event in spawn_events:
        provider_id = event["provider_id"]
        spawn_ts = _parse_ts(event["ts"])
        provider_rows = by_provider.setdefault(provider_id, {"hit": 0, "avoided": 0})
        neighbor_unknown = False
        usability: dict[str, tuple[bool, str | None]] = {}

        for candidate_id in configured_provider_ids:
            snapshot = usage_snapshot_at_or_before(store, candidate_id, spawn_ts, snapshot_max_age)
            if snapshot is None:
                neighbor_unknown = True
                continue
            usable, reason = _snapshot_usability(
                snapshot,
                descriptor_by_id.get(candidate_id),
                spawn_ts,
                min_remaining_percent,
                stale_after_seconds,
            )
            usability[candidate_id] = (usable, reason)

        if neighbor_unknown:
            unknown_spawns += 1

        spawn_usable, spawn_reason = usability.get(provider_id, (False, None))
        others_quota_blocked = any(
            candidate_id != provider_id
            and not usable
            and _is_quota_unusable_reason(reason)
            for candidate_id, (usable, reason) in usability.items()
        )

        if spawn_usable and others_quota_blocked:
            avoided += 1
            provider_rows["avoided"] += 1
        elif not spawn_usable and _is_quota_unusable_reason(spawn_reason):
            hit += 1
            provider_rows["hit"] += 1

    confidence = "low" if unknown_spawns > len(spawn_events) / 2 else "high"
    if confidence == "low":
        data_quality.append(
            {
                "code": "walls_low_confidence",
                "impact": "walls may be undercounted",
                "note": "More than half of spawns lacked fresh neighbor usage snapshots.",
            }
        )

    return {
        "hit": hit,
        "avoided": avoided,
        "by_provider": dict(sorted(by_provider.items())),
        "confidence": confidence,
        "definition": WALLS_DEFINITION,
    }


def _compute_quota(
    store: Store,
    window: Window,
    configured_provider_ids: list[str],
    provider_filter: str | None,
    data_quality: list[dict[str, Any]],
) -> dict[str, Any]:
    provider_ids = [provider_filter] if provider_filter else configured_provider_ids
    quota: dict[str, Any] = {}
    for pid in provider_ids:
        snapshots = usage_snapshots_in_window(store, window, pid)
        if not snapshots:
            data_quality.append(
                {
                    "code": "no_usage_data_for_provider",
                    "provider_id": pid,
                    "impact": "quota and walls undercount",
                }
            )
            continue
        remaining_values = [_minimum_remaining_percent(snapshot) for snapshot in snapshots]
        remaining_values = [value for value in remaining_values if value is not None]
        latest = snapshots[-1]
        latest_remaining = _minimum_remaining_percent(latest)
        quota[pid] = {
            "current_remaining_percent": latest_remaining,
            "min_in_window": min(remaining_values) if remaining_values else None,
            "max_in_window": max(remaining_values) if remaining_values else None,
            "samples": len(snapshots),
        }
    return quota


def _compute_utilization(
    *,
    sessions: list[AgentSession],
    window: Window,
    quota: dict[str, Any],
    sum_worker_hours: float,
) -> dict[str, Any]:
    window_hours = max(0.0, (window.end - window.start).total_seconds() / 3600.0)
    usable_providers = len(quota)
    usable_hours = window_hours * usable_providers if usable_providers else 0.0
    ratio = round(sum_worker_hours / usable_hours, 2) if usable_hours > 0 else None
    return {
        "subscription_utilization": ratio,
        "method": "sum(worker_hours)/sum(usable_hours_in_window)",
    }


def _compute_tokens(
    store: Store,
    configured_provider_ids: list[str],
    descriptor_by_id: dict[str, ProviderDescriptor],
    data_quality: list[dict[str, Any]],
) -> dict[str, Any]:
    by_provider: dict[str, dict[str, int]] = {}
    token_capable_configured = [pid for pid in configured_provider_ids if pid in TOKEN_CAPABLE_PROVIDERS]
    for pid in token_capable_configured:
        snapshots = store.latest_usage_snapshots(pid)
        if not snapshots:
            continue
        snapshot = snapshots[0]
        token_counts = snapshot.raw.get("token_counts") if isinstance(snapshot.raw, dict) else None
        if not isinstance(token_counts, dict):
            continue
        input_tokens = _coerce_int(token_counts.get("input"))
        output_tokens = _coerce_int(token_counts.get("output"))
        if input_tokens is None and output_tokens is None:
            continue
        by_provider[pid] = {
            "input": input_tokens or 0,
            "output": output_tokens or 0,
            "data_quality": "active_5h_block_only",
        }

    providers_without = [pid for pid in configured_provider_ids if pid not in by_provider]
    if by_provider:
        data_quality.append(
            {
                "code": "tokens_partial",
                "providers": sorted(by_provider.keys()),
                "note": "ccusage exposes only current 5h block",
            }
        )
    elif not token_capable_configured:
        data_quality.append(
            {
                "code": "no_token_capable_providers",
                "impact": "tokens null",
                "note": "no configured provider exposes token counts (only claude-code via ccusage in v1)",
            }
        )
    else:
        data_quality.append(
            {
                "code": "no_token_data_available",
                "providers": sorted(token_capable_configured),
                "impact": "tokens null",
                "note": "token-capable providers configured but no token_counts found in latest snapshots",
            }
        )
    totals = {
        "input": sum(row["input"] for row in by_provider.values()),
        "output": sum(row["output"] for row in by_provider.values()),
    }
    return {
        "by_provider": by_provider,
        "totals": totals if by_provider else {"input": None, "output": None},
        "providers_without_token_data": providers_without,
    }


def _suggested_next(window: Window, stats: dict[str, Any]) -> list[str]:
    suggestions: list[str] = []
    if stats.get("parallelism", {}).get("peak_at"):
        suggestions.append(
            "Inspect peak concurrency: agentpool sessions --json (or list_sessions MCP tool)."
        )
    if stats.get("walls", {}).get("avoided"):
        suggestions.append("Review provider distribution with agentpool usage-summary --json.")
    if stats.get("data_quality"):
        suggestions.append("Refresh usage probes before the next delegation window from the CLI when possible.")
    return suggestions


def _snapshot_usability(
    snapshot: CapacitySnapshot,
    descriptor: ProviderDescriptor | None,
    at: datetime,
    min_remaining_percent: int,
    stale_after_seconds: int,
) -> tuple[bool, str | None]:
    windows = [
        {
            "name": window.name,
            "remaining_percent": window.remaining_percent,
        }
        for window in snapshot.windows
    ]
    _ = stale_after_seconds
    return _usable_reason(snapshot, windows, min_remaining_percent, descriptor)


def _is_quota_unusable_reason(reason: str | None) -> bool:
    if reason is None:
        return False
    if reason in QUOTA_REASON_PREFIXES:
        return True
    return "_below_" in reason and reason.endswith("_percent")


def _minimum_remaining_percent(snapshot: CapacitySnapshot) -> float | None:
    values = [window.remaining_percent for window in snapshot.windows if window.remaining_percent is not None]
    return min(values) if values else None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_ts(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return _ensure_utc(parsed)
