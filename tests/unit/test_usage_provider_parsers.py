from __future__ import annotations

from pathlib import Path

from agentpool.models import UsageStatus, UsageWindowKind
from agentpool.usage.provider_parsers import (
    parse_claude_usage,
    parse_codex_status,
    parse_devin_usage,
    parse_droid_status,
    parse_opencode_stats,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "usage"


def test_parse_codex_full_status() -> None:
    snapshot = parse_codex_status(
        "codex-cli",
        """
        5h limit:     [████░░] 70% left (resets 17:39)
        Weekly limit: [██░░░░] 32% left (resets 16:38 on 14 May)
        """,
    )
    assert snapshot is not None
    assert snapshot.status == UsageStatus.AVAILABLE
    assert [w.name for w in snapshot.windows] == ["5h", "weekly"]
    assert [w.kind for w in snapshot.windows] == [UsageWindowKind.FIVE_HOUR, UsageWindowKind.WEEKLY]
    assert snapshot.windows[0].remaining_percent == 70


def test_parse_codex_compact_footer() -> None:
    snapshot = parse_codex_status("codex-cli", "Context 100% left · 5h 69% · weekly 32% · 0.129.0")
    assert snapshot is not None
    assert snapshot.windows[0].name == "5h"
    assert snapshot.windows[1].remaining_percent == 32


def test_parse_claude_usage_panel() -> None:
    snapshot = parse_claude_usage(
        "claude-code",
        """
        Current session
        [████████░░] 82% remaining resets in 3h 42m

        Current week (all models)
        [██████░░░░] 61% left resets in 4d 2h
        """,
    )
    assert snapshot is not None
    assert snapshot.status == UsageStatus.AVAILABLE
    assert [w.name for w in snapshot.windows] == ["session", "weekly"]
    assert [w.kind for w in snapshot.windows] == [UsageWindowKind.SESSION, UsageWindowKind.WEEKLY]
    assert snapshot.windows[0].remaining_percent == 82
    assert snapshot.windows[1].used_percent == 39


def test_parse_claude_usage_used_wording() -> None:
    snapshot = parse_claude_usage(
        "claude-code",
        """
        Current session
        19% used resets in 1h

        Current week (Opus)
        90% used resets in 2d
        """,
    )
    assert snapshot is not None
    assert snapshot.status == UsageStatus.NEAR_LIMIT
    assert snapshot.windows[0].remaining_percent == 81
    assert snapshot.windows[1].name == "weekly_opus"
    assert snapshot.windows[1].remaining_percent == 10


def test_parse_claude_extra_usage() -> None:
    snapshot = parse_claude_usage(
        "claude-code",
        """
        Current session
        0% used
        Extra usage
        ████ 8% used
        $7.93 / $90.00 spent · Resets Jun 1
        """,
    )
    assert snapshot is not None
    assert snapshot.credits_remaining == 82.07
    assert snapshot.windows[-1].name == "extra_usage"
    assert snapshot.windows[-1].kind == UsageWindowKind.ON_DEMAND
    assert snapshot.windows[-1].used_units == 7.93


def test_parse_claude_usage_recorded_fixture() -> None:
    snapshot = parse_claude_usage("claude-code", (FIXTURES / "claude_usage.txt").read_text(encoding="utf-8"))
    assert snapshot is not None
    assert [window.kind for window in snapshot.windows] == [
        UsageWindowKind.SESSION,
        UsageWindowKind.WEEKLY,
        UsageWindowKind.ON_DEMAND,
    ]
    assert snapshot.credits_remaining == 82.07


def test_parse_devin_usage() -> None:
    snapshot = parse_devin_usage(
        "devin-cli",
        "Quota used: 25% (remaining: 75%)\nExtra usage balance: $90.50\nQuota resets May 10, 1:00 AM (UTC-7).",
    )
    assert snapshot is not None
    assert snapshot.credits_remaining == 90.5
    assert snapshot.windows[0].name == "weekly"
    assert snapshot.windows[0].kind == UsageWindowKind.WEEKLY
    assert snapshot.windows[0].remaining_percent == 75
    assert snapshot.windows[0].reset_at is not None


def test_parse_droid_status_is_session_usage_only() -> None:
    snapshot = parse_droid_status(
        "droid-cli",
        "Credit Usage (Current Session):\nInput: 0 credits\nOutput: 1 credits",
    )
    assert snapshot is not None
    assert snapshot.status == UsageStatus.UNKNOWN
    assert snapshot.raw["current_session"]["output"] == 1


def test_parse_opencode_stats_is_local_history_only() -> None:
    snapshot = parse_opencode_stats(
        "opencode",
        "COST & TOKENS\nSessions 382\nMessages 5,011\nTotal Cost $156.49",
    )
    assert snapshot is not None
    assert snapshot.status == UsageStatus.UNKNOWN
    assert snapshot.raw["total_cost"] == 156.49
