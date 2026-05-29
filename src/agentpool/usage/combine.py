from __future__ import annotations

from typing import Any

from agentpool.models import CapacitySnapshot, UsageStatus, UsageWindow
from agentpool.usage._common import _status_from_windows


def combine_usage_snapshots(
    native: CapacitySnapshot,
    codexbar: CapacitySnapshot,
    ccusage: CapacitySnapshot | None = None,
) -> CapacitySnapshot:
    if codexbar.status in {UsageStatus.UNKNOWN, UsageStatus.UNAVAILABLE, UsageStatus.UNAUTHENTICATED}:
        snapshot = native.model_copy(deep=True)
        snapshot.raw["alternate_sources"] = [_usage_source_summary(codexbar)]
        if codexbar.warnings:
            snapshot.warnings.extend(f"CodexBar: {warning}" for warning in codexbar.warnings)
        return _add_ccusage_enrichment(snapshot, ccusage)
    if native.status in {UsageStatus.UNKNOWN, UsageStatus.UNAVAILABLE, UsageStatus.UNAUTHENTICATED}:
        snapshot = codexbar.model_copy(deep=True)
        snapshot.raw["source"] = "combined"
        snapshot.raw["primary_source"] = "codexbar"
        snapshot.raw["alternate_sources"] = [_usage_source_summary(native)]
        if native.warnings:
            snapshot.warnings.extend(f"Native probe: {warning}" for warning in native.warnings)
        return _add_ccusage_enrichment(snapshot, ccusage)
    snapshot = native.model_copy(deep=True)
    existing = {_usage_window_key(window) for window in snapshot.windows}
    for window in codexbar.windows:
        key = _usage_window_key(window)
        if key not in existing:
            snapshot.windows.append(window)
            existing.add(key)
    if snapshot.credits_remaining is None and codexbar.credits_remaining is not None:
        snapshot.credits_remaining = codexbar.credits_remaining
    snapshot.status = _status_from_windows(snapshot.windows)
    snapshot.raw["source"] = "combined"
    snapshot.raw["primary_source"] = native.raw.get("source", "native")
    snapshot.raw["alternate_sources"] = [_usage_source_summary(codexbar)]
    return _add_ccusage_enrichment(snapshot, ccusage)


def _add_ccusage_enrichment(
    snapshot: CapacitySnapshot,
    ccusage: CapacitySnapshot | None = None,
) -> CapacitySnapshot:
    if ccusage is None:
        return snapshot
    enriched = snapshot.model_copy(deep=True)
    enriched.raw.setdefault("alternate_sources", [])
    enriched.raw["alternate_sources"].append(_usage_source_summary(ccusage))
    if ccusage.status in {UsageStatus.UNAVAILABLE, UsageStatus.UNAUTHENTICATED}:
        return enriched
    if ccusage.warnings:
        enriched.warnings.extend(f"ccusage: {warning}" for warning in ccusage.warnings)
    enriched.raw["ccusage"] = ccusage.raw
    return enriched


def _usage_window_key(window: UsageWindow) -> tuple[str, str, str | None]:
    return (window.kind.value, window.name, window.reset_at.isoformat() if window.reset_at else None)


def _usage_source_summary(snapshot: CapacitySnapshot) -> dict[str, Any]:
    return {
        "source": snapshot.raw.get("source"),
        "status": snapshot.status.value if hasattr(snapshot.status, "value") else str(snapshot.status),
        "confidence": snapshot.confidence.value if hasattr(snapshot.confidence, "value") else str(snapshot.confidence),
        "windows": len(snapshot.windows),
    }
