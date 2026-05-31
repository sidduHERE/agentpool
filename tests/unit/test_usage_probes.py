from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentpool.models import UsageStatus, UsageWindowKind
from agentpool.usage import devin as devin_usage
from agentpool.usage.probes import (
    CODEXBAR_PROVIDER_MAP,
    CODEXBAR_SAFE_SOURCE_MAP,
    ProbeError,
    _encode_devin_plan_status_request,
    _extract_json_payload,
    _proto_append_bytes,
    _proto_append_key,
    _proto_append_varint,
    combine_usage_snapshots,
    decode_devin_plan_status_response,
    parse_codex_rate_limits,
    parse_codexbar_usage,
    parse_ccusage_blocks,
    parse_copilot_usage_response,
    parse_devin_plan_status_response,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "usage"


def test_parse_codex_rate_limits_rpc_shape() -> None:
    snapshot = parse_codex_rate_limits(
        "codex-cli",
        {
            "rateLimits": {
                "primary": {
                    "usedPercent": 31,
                    "windowDurationMins": 300,
                    "resetsAt": 1_800_000_000,
                },
                "secondary": {
                    "usedPercent": 68,
                    "windowDurationMins": 10080,
                    "resetsAt": 1_800_100_000,
                },
                "credits": {"hasCredits": True, "unlimited": False, "balance": "12.50"},
            }
        },
    )
    assert snapshot.status == UsageStatus.AVAILABLE
    assert [w.name for w in snapshot.windows] == ["5h", "weekly"]
    assert snapshot.windows[0].remaining_percent == 69
    assert snapshot.windows[1].reset_at == datetime.fromtimestamp(1_800_100_000, tz=UTC)
    assert snapshot.credits_remaining == 12.5


def test_parse_codex_rate_limits_recorded_fixture() -> None:
    payload = json.loads((FIXTURES / "codex_rate_limits.json").read_text(encoding="utf-8"))

    snapshot = parse_codex_rate_limits("codex-cli", payload)

    assert [window.kind for window in snapshot.windows] == [
        UsageWindowKind.FIVE_HOUR,
        UsageWindowKind.WEEKLY,
    ]
    assert snapshot.windows[0].remaining_percent == 55
    assert snapshot.windows[1].remaining_percent == 29


def test_parse_codexbar_codex_cli_usage_with_noise() -> None:
    payload = _extract_json_payload("[codex notify] remoteControl/status/changed\n[{\"provider\":\"codex\"}]\n")
    assert payload == [{"provider": "codex"}]

    snapshot = parse_codexbar_usage(
        "codex-cli",
        [
            {
                "provider": "codex",
                "source": "codex-cli",
                "usage": {
                    "accountEmail": "user@example.com",
                    "loginMethod": "pro",
                    "primary": {
                        "usedPercent": 68,
                        "windowMinutes": 300,
                        "resetsAt": "2026-05-09T00:39:26Z",
                    },
                    "secondary": {
                        "usedPercent": 76,
                        "windowMinutes": 10080,
                        "resetsAt": "2026-05-14T23:38:57Z",
                    },
                    "tertiary": None,
                },
                "credits": {"remaining": 12.5},
                "version": "0.129.0",
            }
        ],
        expected_provider="codex",
    )

    assert snapshot.status == UsageStatus.AVAILABLE
    assert [window.kind for window in snapshot.windows] == [
        UsageWindowKind.FIVE_HOUR,
        UsageWindowKind.WEEKLY,
    ]
    assert snapshot.windows[0].remaining_percent == 32
    assert snapshot.credits_remaining == 12.5
    assert snapshot.raw["login_method"] == "pro"


def test_parse_ccusage_blocks_active_block_is_observed_not_quota() -> None:
    snapshot = parse_ccusage_blocks(
        "claude-code",
        {
            "blocks": [
                {
                    "id": "2026-05-11T02:00:00.000Z",
                    "startTime": "2026-05-11T02:00:00.000Z",
                    "endTime": "2026-05-11T07:00:00.000Z",
                    "actualEndTime": "2026-05-11T02:55:39.709Z",
                    "isActive": True,
                    "isGap": False,
                    "entries": 89,
                    "tokenCounts": {"inputTokens": 10, "outputTokens": 20},
                    "totalTokens": 6098210,
                    "costUSD": 7.55,
                    "models": ["claude-opus-4-7"],
                    "burnRate": {"costPerHour": 10.0},
                    "projection": {"remainingMinutes": 229, "totalTokens": 37_000_000},
                }
            ]
        },
    )

    assert snapshot.status == UsageStatus.UNKNOWN
    assert snapshot.confidence.value == "observed"
    assert snapshot.windows[0].name == "active_block"
    assert snapshot.windows[0].remaining_percent is None
    assert snapshot.windows[0].used_units == 6098210
    assert snapshot.raw["source"] == "ccusage_local_logs"
    assert "not authoritative quota" in snapshot.warnings[0]


def test_parse_ccusage_blocks_token_limit_status_is_user_threshold() -> None:
    snapshot = parse_ccusage_blocks(
        "claude-code",
        {
            "blocks": [
                {
                    "id": "active",
                    "endTime": "2026-05-11T07:00:00.000Z",
                    "isActive": True,
                    "isGap": False,
                    "totalTokens": 50,
                    "tokenLimitStatus": {
                        "limit": 100,
                        "projectedUsage": 80,
                        "percentUsed": 80,
                        "status": "warning",
                    },
                }
            ]
        },
    )

    assert snapshot.windows[0].used_percent == 80
    assert snapshot.raw["token_limit_status"]["status"] == "warning"


def test_parse_ccusage_blocks_rejects_missing_blocks() -> None:
    with pytest.raises(ProbeError):
        parse_ccusage_blocks("claude-code", {})


def test_combine_usage_snapshots_uses_codexbar_fallback() -> None:
    native = parse_codex_rate_limits(
        "codex-cli",
        {"rateLimits": {"primary": {"usedPercent": 99, "windowDurationMins": 300}}},
    )
    codexbar = parse_codexbar_usage(
        "codex-cli",
        [{"provider": "codex", "usage": {"secondary": {"usedPercent": 20, "windowMinutes": 10080}}}],
        expected_provider="codex",
    )

    combined = combine_usage_snapshots(native, codexbar)

    assert combined.raw["source"] == "combined"
    assert [window.kind for window in combined.windows] == [
        UsageWindowKind.FIVE_HOUR,
        UsageWindowKind.WEEKLY,
    ]


def test_codexbar_cursor_usage_mapping_and_parse() -> None:
    assert CODEXBAR_PROVIDER_MAP["cursor-cli"] == "cursor"
    assert CODEXBAR_SAFE_SOURCE_MAP["cursor-cli"] == "cli"

    snapshot = parse_codexbar_usage(
        "cursor-cli",
        [
            {
                "provider": "cursor",
                "source": "web",
                "usage": {
                    "primary": {"usedPercent": 0, "resetsAt": "2026-06-19T03:03:30Z"},
                    "secondary": {"usedPercent": 25, "resetsAt": "2026-06-19T03:03:30Z"},
                    "loginMethod": "Cursor Free",
                },
            }
        ],
        expected_provider="cursor",
    )

    assert snapshot.provider_id == "cursor-cli"
    assert snapshot.status == UsageStatus.AVAILABLE
    assert [window.name for window in snapshot.windows] == ["primary", "secondary"]
    assert snapshot.windows[1].remaining_percent == 75
    assert snapshot.raw["codexbar_source"] == "web"


def test_parse_copilot_quota_snapshots() -> None:
    snapshot = parse_copilot_usage_response(
        "copilot-cli",
        {
            "copilot_plan": "business",
            "quota_reset_date": "2026-06-01T00:00:00Z",
            "quota_snapshots": {
                "premium_interactions": {
                    "entitlement": 300,
                    "remaining": 150,
                    "percent_remaining": 50,
                    "quota_id": "premium",
                },
                "chat": {
                    "entitlement": 1000,
                    "remaining": 800,
                    "percent_remaining": 80,
                    "quota_id": "chat",
                },
            },
        },
    )
    assert snapshot.status == UsageStatus.AVAILABLE
    assert [w.name for w in snapshot.windows] == ["premium_interactions", "chat"]
    assert snapshot.windows[0].used_percent == 50
    assert snapshot.windows[1].remaining_units == 800
    assert snapshot.raw["plan"] == "business"


def test_parse_copilot_usage_recorded_fixture() -> None:
    payload = json.loads((FIXTURES / "copilot_user.json").read_text(encoding="utf-8"))

    snapshot = parse_copilot_usage_response("copilot-cli", payload)

    assert [window.kind for window in snapshot.windows] == [
        UsageWindowKind.MONTHLY,
        UsageWindowKind.MONTHLY,
    ]
    assert snapshot.windows[0].remaining_percent == 91.1
    assert snapshot.windows[1].remaining_units == 1000


def test_parse_copilot_legacy_monthly_limited_counts() -> None:
    snapshot = parse_copilot_usage_response(
        "copilot-cli",
        {
            "copilot_plan": "individual",
            "monthly_quotas": {"chat": 1000, "completions": 300},
            "limited_user_quotas": {"chat": 100, "completions": 30},
        },
    )
    assert snapshot.status == UsageStatus.NEAR_LIMIT
    assert snapshot.windows[0].name == "premium_interactions"
    assert snapshot.windows[0].remaining_percent == 10


def test_devin_plan_status_request_contains_auth_and_top_up_flag() -> None:
    data = _encode_devin_plan_status_request("devin-session-token$abc")
    assert b"devin-session-token$abc" in data
    assert data.endswith(b"\x10\x01")


def test_devin_usage_disables_interactive_fallback_for_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_plan_status(provider_id: str):
        raise devin_usage.ProbeError("missing credentials")

    def explode_tmux_probe(*args, **kwargs):
        raise AssertionError("interactive Devin /usage fallback should not run")

    monkeypatch.setattr(devin_usage.shutil, "which", lambda _: "/bin/devin")
    monkeypatch.setattr(devin_usage, "_devin_plan_status_usage_snapshot", fail_plan_status)
    monkeypatch.setattr(devin_usage, "_tmux_slash_usage_probe", explode_tmux_probe)

    snapshot = devin_usage.devin_cli_usage_snapshot(
        "devin-cli",
        allow_interactive_fallback=False,
    )

    assert snapshot.status == UsageStatus.UNKNOWN
    assert snapshot.raw["source"] == "interactive_probe_disabled"


def test_parse_devin_plan_status_response() -> None:
    payload = json.loads((FIXTURES / "devin_plan_status.json").read_text(encoding="utf-8"))

    snapshot = parse_devin_plan_status_response("devin-cli", payload)

    assert snapshot.status == UsageStatus.AVAILABLE
    assert [(w.name, w.kind, w.used_percent, w.remaining_percent) for w in snapshot.windows] == [
        ("daily", UsageWindowKind.DAILY, 7, 93),
        ("weekly", UsageWindowKind.WEEKLY, 25, 75),
    ]
    assert snapshot.credits_remaining == 90.498189
    assert snapshot.raw["plan_name"] == "Trial"


def test_decode_devin_plan_status_proto_response() -> None:
    plan_info = _proto_message(
        [
            (1, "varint", 20),
            (2, "bytes", b"Trial"),
        ]
    )
    daily_reset = _timestamp_message(1_778_313_600)
    weekly_reset = _timestamp_message(1_778_400_000)
    plan = _proto_message(
        [
            (1, "bytes", plan_info),
            (14, "varint", 93),
            (15, "varint", 75),
            (16, "varint", 90_498_189),
            (17, "varint", 1_778_313_600),
            (18, "varint", 1_778_400_000),
            (2, "bytes", _timestamp_message(1_778_212_077)),
            (3, "bytes", weekly_reset),
        ]
    )
    response = _proto_message([(1, "bytes", plan)])
    payload = decode_devin_plan_status_response(response)
    assert payload["plan_status"]["plan_info"]["plan_name"] == "Trial"
    assert payload["plan_status"]["daily_remaining_percent"] == 93
    assert payload["plan_status"]["weekly_reset_at_unix"] == 1_778_400_000
    assert daily_reset


def _timestamp_message(seconds: int) -> bytes:
    return _proto_message([(1, "varint", seconds)])


def _proto_message(fields: list[tuple[int, str, int | bytes]]) -> bytes:
    data = bytearray()
    for field_number, kind, value in fields:
        if kind == "varint":
            assert isinstance(value, int)
            _proto_append_key(data, field_number, 0)
            _proto_append_varint(data, value)
        elif kind == "bytes":
            assert isinstance(value, bytes)
            _proto_append_key(data, field_number, 2)
            _proto_append_bytes(data, value)
        else:
            raise AssertionError(f"unexpected test proto field kind {kind}")
    return bytes(data)
