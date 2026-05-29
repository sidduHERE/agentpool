from __future__ import annotations

from datetime import timedelta

from agentpool.models import (
    AuthStatus,
    CapacitySnapshot,
    Confidence,
    ProviderDescriptor,
    UsageStatus,
    UsageWindow,
    UsageWindowKind,
    now_utc,
)
from agentpool.usage.summary import build_usage_summary


def test_usage_summary_is_keyed_by_provider_and_marks_usable() -> None:
    summary = build_usage_summary(
        [
            CapacitySnapshot(
                provider_id="codex-cli",
                status=UsageStatus.AVAILABLE,
                confidence=Confidence.OFFICIAL,
                windows=[
                    UsageWindow(
                        name="5h",
                        kind=UsageWindowKind.FIVE_HOUR,
                        remaining_percent=80,
                        confidence=Confidence.OFFICIAL,
                    ),
                    UsageWindow(
                        name="weekly",
                        kind=UsageWindowKind.WEEKLY,
                        remaining_percent=12,
                        confidence=Confidence.OFFICIAL,
                    ),
                ],
            )
        ],
        min_remaining_percent=10,
        stale_after_seconds=1800,
        provider_descriptors=[_descriptor("codex-cli")],
    )

    assert set(summary["providers"]) == {"codex-cli"}
    assert summary["providers"]["codex-cli"]["usable"] is True
    assert summary["providers"]["codex-cli"]["unusable_reason"] is None


def test_usage_summary_buffer_applies_to_every_window() -> None:
    reset_at = now_utc() + timedelta(hours=1)
    summary = build_usage_summary(
        [
            CapacitySnapshot(
                provider_id="codex-cli",
                status=UsageStatus.AVAILABLE,
                confidence=Confidence.OFFICIAL,
                windows=[
                    UsageWindow(
                        name="5h",
                        kind=UsageWindowKind.FIVE_HOUR,
                        remaining_percent=80,
                        confidence=Confidence.OFFICIAL,
                    ),
                    UsageWindow(
                        name="weekly",
                        kind=UsageWindowKind.WEEKLY,
                        remaining_percent=8,
                        reset_at=reset_at,
                        confidence=Confidence.OFFICIAL,
                    ),
                ],
            )
        ],
        min_remaining_percent=10,
        stale_after_seconds=1800,
        provider_descriptors=[_descriptor("codex-cli")],
    )

    row = summary["providers"]["codex-cli"]
    assert row["usable"] is False
    assert row["unusable_reason"] == "weekly_below_10_percent"
    assert summary["next_available_provider"] == "codex-cli"
    assert summary["next_available_at"] == reset_at.isoformat()


def test_usage_summary_auth_install_and_unknown_confidence_block_usable() -> None:
    summary = build_usage_summary(
        [
            CapacitySnapshot(
                provider_id="claude-code",
                status=UsageStatus.AVAILABLE,
                confidence=Confidence.UNKNOWN,
                windows=[],
            ),
            CapacitySnapshot(
                provider_id="missing-cli",
                status=UsageStatus.AVAILABLE,
                confidence=Confidence.OFFICIAL,
                windows=[],
            ),
            CapacitySnapshot(
                provider_id="logged-out",
                status=UsageStatus.AVAILABLE,
                confidence=Confidence.OFFICIAL,
                windows=[],
            ),
        ],
        provider_descriptors=[
            _descriptor("claude-code"),
            _descriptor("missing-cli", installed=False),
            _descriptor("logged-out", auth_status="unauthenticated"),
        ],
    )

    assert summary["providers"]["claude-code"]["unusable_reason"] == "confidence_unknown"
    assert summary["providers"]["missing-cli"]["unusable_reason"] == "not_installed"
    assert summary["providers"]["logged-out"]["unusable_reason"] == "auth_unauthenticated"
    assert summary["counts"]["usable"] == 0


def test_usage_summary_marks_stale_snapshots_unusable() -> None:
    snapshot = CapacitySnapshot(
        provider_id="codex-cli",
        status=UsageStatus.AVAILABLE,
        confidence=Confidence.OFFICIAL,
        checked_at=now_utc() - timedelta(seconds=3600),
        windows=[
            UsageWindow(
                name="5h",
                kind=UsageWindowKind.FIVE_HOUR,
                remaining_percent=90,
                confidence=Confidence.OFFICIAL,
            )
        ],
    )

    summary = build_usage_summary([snapshot], stale_after_seconds=1800, provider_descriptors=[_descriptor("codex-cli")])

    assert summary["providers"]["codex-cli"]["usable"] is False
    assert summary["providers"]["codex-cli"]["unusable_reason"] == "usage_stale"
    assert summary["providers"]["codex-cli"]["stale"] is True


def _descriptor(provider_id: str, installed: bool = True, auth_status: str = "authenticated") -> ProviderDescriptor:
    return ProviderDescriptor(
        id=provider_id,
        display_name=provider_id,
        harness=provider_id,
        installed=installed,
        auth=AuthStatus(status=auth_status, confidence=Confidence.LOCAL_CONFIG),
    )
