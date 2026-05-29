from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agentpool.models import CapacitySnapshot, Confidence, ProviderDescriptor, UsageStatus


def build_usage_summary(
    snapshots: list[CapacitySnapshot],
    min_remaining_percent: int = 10,
    stale_after_seconds: int = 1800,
    provider_descriptors: list[ProviderDescriptor] | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    descriptor_by_id = {descriptor.id: descriptor for descriptor in provider_descriptors or []}
    rows = {
        snapshot.provider_id: _provider_summary(
            snapshot,
            min_remaining_percent=min_remaining_percent,
            stale_after_seconds=stale_after_seconds,
            now=now,
            descriptor=descriptor_by_id.get(snapshot.provider_id),
        )
        for snapshot in sorted(snapshots, key=lambda item: item.provider_id)
    }
    next_provider, next_at = _next_available(rows)
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "providers": rows,
        "min_remaining_percent": min_remaining_percent,
        "next_available_provider": next_provider,
        "next_available_at": next_at,
        "counts": {
            "total": len(rows),
            "usable": sum(1 for row in rows.values() if row["usable"]),
            "unusable": sum(1 for row in rows.values() if not row["usable"]),
            "available": sum(1 for row in rows.values() if row["status"] == UsageStatus.AVAILABLE.value),
            "near_limit": sum(1 for row in rows.values() if row["status"] == UsageStatus.NEAR_LIMIT.value),
            "limit_reached": sum(1 for row in rows.values() if row["status"] == UsageStatus.LIMIT_REACHED.value),
            "unknown": sum(1 for row in rows.values() if row["status"] == UsageStatus.UNKNOWN.value),
            "unavailable": sum(1 for row in rows.values() if row["status"] == UsageStatus.UNAVAILABLE.value),
        },
    }


def _provider_summary(
    snapshot: CapacitySnapshot,
    min_remaining_percent: int,
    stale_after_seconds: int,
    now: datetime,
    descriptor: ProviderDescriptor | None = None,
) -> dict[str, Any]:
    windows = [
        {
            "name": window.name,
            "kind": window.kind.value if hasattr(window.kind, "value") else str(window.kind),
            "remaining_percent": window.remaining_percent,
            "used_percent": window.used_percent,
            "remaining_units": window.remaining_units,
            "reset_at": window.reset_at.isoformat() if window.reset_at else None,
            "confidence": window.confidence.value if hasattr(window.confidence, "value") else str(window.confidence),
        }
        for window in snapshot.windows
    ]
    age_seconds = max(0.0, (now - snapshot.checked_at).total_seconds())
    stale = age_seconds > stale_after_seconds
    usable, unusable_reason = _usable_reason(snapshot, windows, min_remaining_percent, descriptor, stale)
    return {
        "provider_id": snapshot.provider_id,
        "status": snapshot.status.value if hasattr(snapshot.status, "value") else str(snapshot.status),
        "confidence": snapshot.confidence.value if hasattr(snapshot.confidence, "value") else str(snapshot.confidence),
        "installed": descriptor.installed if descriptor else None,
        "auth_status": descriptor.auth.status if descriptor else None,
        "usable": usable,
        "unusable_reason": unusable_reason,
        "stale": stale,
        "age_seconds": age_seconds,
        "checked_at": snapshot.checked_at.isoformat(),
        "windows": windows,
        "credits_remaining": snapshot.credits_remaining,
        "warnings": snapshot.warnings,
        "source": snapshot.raw.get("source"),
        "summary": _summary_text(snapshot, windows),
    }


def _usable_reason(
    snapshot: CapacitySnapshot,
    windows: list[dict[str, Any]],
    min_remaining_percent: int,
    descriptor: ProviderDescriptor | None,
    stale: bool,
) -> tuple[bool, str | None]:
    if descriptor and not descriptor.installed:
        return False, "not_installed"
    if descriptor and descriptor.auth.status in {"unauthenticated", "unavailable"}:
        return False, f"auth_{descriptor.auth.status}"
    status = snapshot.status.value if hasattr(snapshot.status, "value") else str(snapshot.status)
    if status in {UsageStatus.LIMIT_REACHED.value, UsageStatus.UNAVAILABLE.value, UsageStatus.UNAUTHENTICATED.value}:
        return False, status
    if status == UsageStatus.UNKNOWN.value:
        return False, "usage_unknown"
    confidence = snapshot.confidence.value if hasattr(snapshot.confidence, "value") else str(snapshot.confidence)
    allowed_confidence = {
        Confidence.OFFICIAL.value,
        Confidence.LOCAL_CLI.value,
        Confidence.LOCAL_CONFIG.value,
        Confidence.USER_CONFIGURED.value,
    }
    if confidence not in allowed_confidence:
        return False, f"confidence_{confidence}"
    if stale:
        return False, "usage_stale"
    for window in windows:
        remaining = window["remaining_percent"]
        if remaining is not None and remaining < min_remaining_percent:
            return False, f"{window['name']}_below_{min_remaining_percent}_percent"
    return True, None


def _next_available(rows: dict[str, dict[str, Any]]) -> tuple[str | None, str | None]:
    candidates: list[tuple[datetime, str]] = []
    now = datetime.now(timezone.utc)
    for provider_id, row in rows.items():
        if row["usable"]:
            continue
        for window in row["windows"]:
            reset_at = window.get("reset_at")
            if not reset_at:
                continue
            try:
                parsed = datetime.fromisoformat(reset_at)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed > now:
                candidates.append((parsed, provider_id))
    if not candidates:
        return None, None
    reset_at, provider_id = min(candidates, key=lambda item: item[0])
    return provider_id, reset_at.isoformat()


def _summary_text(snapshot: CapacitySnapshot, windows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    seen: set[tuple[str, float | None, float | None]] = set()
    for window in windows:
        label = window["kind"] if window["kind"] != "unknown" else window["name"]
        remaining = window["remaining_percent"]
        key = (label, remaining, window["remaining_units"])
        if key in seen:
            continue
        seen.add(key)
        if remaining is not None:
            parts.append(f"{label} {remaining:g}% left")
        elif window["remaining_units"] is not None:
            parts.append(f"{label} {window['remaining_units']:g} units left")
    if snapshot.credits_remaining is not None:
        parts.append(f"${snapshot.credits_remaining:g} credits")
    if parts:
        return ", ".join(parts)
    if snapshot.warnings:
        return "; ".join(snapshot.warnings)
    return snapshot.status.value if hasattr(snapshot.status, "value") else str(snapshot.status)
