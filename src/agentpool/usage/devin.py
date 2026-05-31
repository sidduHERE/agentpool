from __future__ import annotations

import shutil
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agentpool.models import CapacitySnapshot, Confidence, UsageWindow, UsageWindowKind
from agentpool.usage._common import (
    ProbeError,
    _clamp_percent,
    _epoch_seconds,
    _number,
    _tmux_slash_usage_probe,
    _clean_optional_string,
    _status_from_windows,
    unavailable,
    unknown,
)
from agentpool.usage.provider_parsers import parse_devin_usage


def devin_cli_usage_snapshot(
    provider_id: str,
    binary: str | None = None,
    *,
    allow_interactive_fallback: bool = True,
) -> CapacitySnapshot:
    executable = binary or shutil.which("devin")
    if not executable:
        return unavailable(provider_id, "Devin CLI is not installed.")
    try:
        return _devin_plan_status_usage_snapshot(provider_id)
    except ProbeError as exc:
        if not allow_interactive_fallback:
            return unknown(
                provider_id,
                "Devin plan-status API probe failed and interactive CLI /usage fallback "
                f"is disabled for MCP callers: {exc}",
                source="interactive_probe_disabled",
            )
        fallback_warning = f"Devin plan-status API probe failed; fell back to CLI /usage: {exc}"
    snapshot = _tmux_slash_usage_probe(
        provider_id=provider_id,
        command=[executable, "--permission-mode", "auto"],
        slash_command="/usage",
        parser=parse_devin_usage,
        source="devin_pty_usage",
        startup_delay=1.0,
        timeout=18.0,
        pre_keys=[["Enter"]],
        prefer_text="Quota used:",
    )
    snapshot.warnings.append(fallback_warning)
    return snapshot


def _devin_plan_status_usage_snapshot(provider_id: str) -> CapacitySnapshot:
    creds = _load_devin_cli_credentials()
    token = _clean_optional_string(creds.get("windsurf_api_key"))
    if not token:
        raise ProbeError("Devin CLI credentials do not contain windsurf_api_key.")
    api_server_url = _clean_optional_string(creds.get("api_server_url")) or "https://server.codeium.com"
    endpoint = api_server_url.rstrip("/") + "/exa.seat_management_pb.SeatManagementService/GetPlanStatus"
    request = urllib.request.Request(
        endpoint,
        data=_encode_devin_plan_status_request(token),
        headers={
            "Content-Type": "application/proto",
            "Connect-Protocol-Version": "1",
            "Origin": "https://windsurf.com",
            "Referer": "https://windsurf.com/profile",
            "User-Agent": "agentpool-devin-probe/0.1",
            "x-auth-token": token,
            "x-devin-session-token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500].replace(token, "<redacted>")
        raise ProbeError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ProbeError(str(exc.reason)) from exc
    payload = decode_devin_plan_status_response(data)
    snapshot = parse_devin_plan_status_response(provider_id, payload)
    snapshot.raw["source"] = "devin_plan_status_api"
    snapshot.raw["credential_source"] = "devin_cli_credentials"
    return snapshot


def _load_devin_cli_credentials() -> dict[str, Any]:
    path = Path("~/.local/share/devin/credentials.toml").expanduser()
    if not path.exists():
        raise ProbeError("Devin CLI credentials were not found. Run `devin auth login` first.")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProbeError(f"Could not read Devin CLI credentials: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProbeError("Devin CLI credentials did not parse as a TOML table.")
    return raw


def parse_devin_plan_status_response(provider_id: str, payload: dict[str, Any]) -> CapacitySnapshot:
    plan_status = payload.get("plan_status")
    if not isinstance(plan_status, dict):
        raise ProbeError("Devin plan-status response did not include plan_status.")
    windows: list[UsageWindow] = []
    for name, remaining_key, reset_key in (
        ("daily", "daily_remaining_percent", "daily_reset_at_unix"),
        ("weekly", "weekly_remaining_percent", "weekly_reset_at_unix"),
    ):
        remaining = _number(plan_status.get(remaining_key))
        if remaining is None:
            continue
        windows.append(
            UsageWindow(
                name=name,
                kind=UsageWindowKind(name),
                status=name,
                used_percent=_clamp_percent(100.0 - remaining),
                remaining_percent=_clamp_percent(remaining),
                reset_at=_epoch_seconds(plan_status.get(reset_key)),
                confidence=Confidence.OFFICIAL,
            )
        )
    overage_balance_micros = _number(plan_status.get("overage_balance_micros"))
    credits_remaining = overage_balance_micros / 1_000_000 if overage_balance_micros is not None else None
    if not windows and credits_remaining is None:
        raise ProbeError("Devin plan-status response did not include quota windows or overage balance.")
    plan_info = plan_status.get("plan_info") if isinstance(plan_status.get("plan_info"), dict) else {}
    raw: dict[str, Any] = {
        "plan_name": _clean_optional_string(plan_info.get("plan_name")),
        "teams_tier": plan_info.get("teams_tier"),
        "plan_start": _epoch_seconds(plan_status.get("plan_start_unix")).isoformat()
        if _epoch_seconds(plan_status.get("plan_start_unix"))
        else None,
        "plan_end": _epoch_seconds(plan_status.get("plan_end_unix")).isoformat()
        if _epoch_seconds(plan_status.get("plan_end_unix"))
        else None,
        "has_overage_balance": overage_balance_micros is not None,
    }
    return CapacitySnapshot(
        provider_id=provider_id,
        status=_status_from_windows(windows),
        confidence=Confidence.OFFICIAL,
        windows=windows,
        credits_remaining=credits_remaining,
        raw={key: value for key, value in raw.items() if value is not None},
    )


def _encode_devin_plan_status_request(auth_token: str) -> bytes:
    data = bytearray()
    _proto_append_key(data, 1, 2)
    _proto_append_bytes(data, auth_token.encode("utf-8"))
    _proto_append_key(data, 2, 0)
    _proto_append_varint(data, 1)
    return bytes(data)


def decode_devin_plan_status_response(data: bytes) -> dict[str, Any]:
    plan_status: dict[str, Any] | None = None
    for field_number, wire_type, value in _proto_fields(data):
        if field_number == 1 and wire_type == 2 and isinstance(value, bytes):
            plan_status = _decode_devin_plan_status(value)
    return {"plan_status": plan_status} if plan_status is not None else {}


def _decode_devin_plan_status(data: bytes) -> dict[str, Any]:
    plan_status: dict[str, Any] = {}
    for field_number, wire_type, value in _proto_fields(data):
        if field_number == 1 and wire_type == 2 and isinstance(value, bytes):
            plan_status["plan_info"] = _decode_devin_plan_info(value)
        elif field_number == 2 and wire_type == 2 and isinstance(value, bytes):
            plan_status["plan_start_unix"] = _decode_timestamp_seconds(value)
        elif field_number == 3 and wire_type == 2 and isinstance(value, bytes):
            plan_status["plan_end_unix"] = _decode_timestamp_seconds(value)
        elif field_number == 14 and wire_type == 0:
            plan_status["daily_remaining_percent"] = value
        elif field_number == 15 and wire_type == 0:
            plan_status["weekly_remaining_percent"] = value
        elif field_number == 16 and wire_type == 0:
            plan_status["overage_balance_micros"] = value
        elif field_number == 17 and wire_type == 0:
            plan_status["daily_reset_at_unix"] = value
        elif field_number == 18 and wire_type == 0:
            plan_status["weekly_reset_at_unix"] = value
    return plan_status


def _decode_devin_plan_info(data: bytes) -> dict[str, Any]:
    plan_info: dict[str, Any] = {}
    for field_number, wire_type, value in _proto_fields(data):
        if field_number == 1 and wire_type == 0:
            plan_info["teams_tier"] = value
        elif field_number == 2 and wire_type == 2 and isinstance(value, bytes):
            plan_info["plan_name"] = value.decode("utf-8", errors="replace")
    return plan_info


def _decode_timestamp_seconds(data: bytes) -> int | None:
    for field_number, wire_type, value in _proto_fields(data):
        if field_number == 1 and wire_type == 0:
            return int(value)
    return None


def _proto_fields(data: bytes) -> list[tuple[int, int, int | bytes]]:
    fields: list[tuple[int, int, int | bytes]] = []
    index = 0
    while index < len(data):
        key, index = _proto_read_varint(data, index)
        field_number = key >> 3
        wire_type = key & 0x07
        if wire_type == 0:
            value, index = _proto_read_varint(data, index)
            fields.append((field_number, wire_type, value))
        elif wire_type == 1:
            if index + 8 > len(data):
                raise ProbeError("Truncated fixed64 protobuf field.")
            fields.append((field_number, wire_type, data[index : index + 8]))
            index += 8
        elif wire_type == 2:
            length, index = _proto_read_varint(data, index)
            end = index + length
            if end > len(data):
                raise ProbeError("Truncated length-delimited protobuf field.")
            fields.append((field_number, wire_type, data[index:end]))
            index = end
        elif wire_type == 5:
            if index + 4 > len(data):
                raise ProbeError("Truncated fixed32 protobuf field.")
            fields.append((field_number, wire_type, data[index : index + 4]))
            index += 4
        else:
            raise ProbeError(f"Unsupported protobuf wire type {wire_type}.")
    return fields


def _proto_read_varint(data: bytes, index: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while index < len(data):
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index
        shift += 7
        if shift > 70:
            raise ProbeError("Malformed protobuf varint.")
    raise ProbeError("Truncated protobuf varint.")


def _proto_append_key(data: bytearray, field_number: int, wire_type: int) -> None:
    _proto_append_varint(data, (field_number << 3) | wire_type)


def _proto_append_bytes(data: bytearray, value: bytes) -> None:
    _proto_append_varint(data, len(value))
    data.extend(value)


def _proto_append_varint(data: bytearray, value: int) -> None:
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            data.append(byte | 0x80)
        else:
            data.append(byte)
            return
