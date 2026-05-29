from __future__ import annotations

import json
import select
import shutil
import subprocess
import time
from typing import Any

from agentpool.models import CapacitySnapshot, Confidence, UsageWindow, UsageWindowKind
from agentpool.usage._common import (
    ProbeError,
    _clamp_percent,
    _epoch_seconds,
    _int_number,
    _number,
    _safe_read_pipe,
    _status_from_windows,
    _terminate_process,
    unavailable,
    unknown,
)


def codex_cli_usage_snapshot(provider_id: str, binary: str | None = None) -> CapacitySnapshot:
    executable = binary or shutil.which("codex")
    if not executable:
        return unavailable(provider_id, "Codex CLI is not installed.")
    try:
        response = _codex_rpc_rate_limits(executable)
        snapshot = parse_codex_rate_limits(provider_id, response)
        snapshot.raw["source"] = "codex_app_server"
        return snapshot
    except ProbeError as exc:
        recovered = _recover_codex_error_snapshot(provider_id, str(exc))
        if recovered:
            recovered.warnings.append("Codex app-server returned usage inside an error response.")
            return recovered
        return unknown(provider_id, f"Codex app-server usage probe failed: {exc}", source="codex_app_server")


def parse_codex_rate_limits(provider_id: str, payload: dict[str, Any]) -> CapacitySnapshot:
    rate_limits = payload.get("rateLimits") if isinstance(payload.get("rateLimits"), dict) else payload
    windows: list[UsageWindow] = []
    for fallback_name, item in (("primary", rate_limits.get("primary")), ("secondary", rate_limits.get("secondary"))):
        if not isinstance(item, dict):
            continue
        used = _number(item.get("usedPercent"))
        if used is None:
            continue
        duration = _int_number(item.get("windowDurationMins"))
        name = _codex_window_name(fallback_name, duration)
        reset_at = _epoch_seconds(item.get("resetsAt"))
        windows.append(
            UsageWindow(
                name=name,
                kind=_codex_window_kind(name),
                used_percent=_clamp_percent(used),
                remaining_percent=_clamp_percent(100.0 - used),
                reset_at=reset_at,
                confidence=Confidence.OFFICIAL,
                raw_text=f"{fallback_name}:{duration or 'unknown'}",
            )
        )
    credits_remaining = None
    credits = rate_limits.get("credits")
    if isinstance(credits, dict) and not credits.get("unlimited"):
        credits_remaining = _number(credits.get("balance"))
    if not windows and credits_remaining is None:
        raise ProbeError("No rate-limit windows or credits in Codex response.")
    return CapacitySnapshot(
        provider_id=provider_id,
        status=_status_from_windows(windows),
        confidence=Confidence.OFFICIAL,
        windows=windows,
        credits_remaining=credits_remaining,
        raw={"credits": _safe_credit_summary(credits) if isinstance(credits, dict) else None},
    )


def _codex_rpc_rate_limits(executable: str) -> dict[str, Any]:
    proc = subprocess.Popen(
        [executable, "-s", "read-only", "-a", "untrusted", "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _json_rpc_request(proc, 1, "initialize", {"clientInfo": {"name": "agentpool", "version": "0.1.0"}}, 8.0)
        _json_rpc_notify(proc, "initialized")
        return _json_rpc_request(proc, 2, "account/rateLimits/read", {}, 4.0)
    finally:
        _terminate_process(proc)


def _json_rpc_request(
    proc: subprocess.Popen[str],
    request_id: int,
    method: str,
    params: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    _write_json_line(proc, {"id": request_id, "method": method, "params": params})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr = _safe_read_pipe(proc.stderr)
            raise ProbeError(f"codex app-server exited during {method}: {stderr}".strip())
        remaining = max(0.05, deadline - time.monotonic())
        assert proc.stdout is not None
        ready, _, _ = select.select([proc.stdout], [], [], remaining)
        if not ready:
            continue
        line = proc.stdout.readline()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") != request_id:
            continue
        if "error" in message:
            error = message["error"]
            raise ProbeError(error.get("message", str(error)) if isinstance(error, dict) else str(error))
        result = message.get("result")
        if not isinstance(result, dict):
            raise ProbeError(f"JSON-RPC {method} returned no object result.")
        return result
    _terminate_process(proc)
    raise ProbeError(f"codex app-server timed out during {method}.")


def _json_rpc_notify(proc: subprocess.Popen[str], method: str) -> None:
    _write_json_line(proc, {"method": method, "params": {}})


def _write_json_line(proc: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise ProbeError("process stdin is unavailable")
    proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def _recover_codex_error_snapshot(provider_id: str, message: str) -> CapacitySnapshot | None:
    start = message.find("{")
    end = message.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(message[start : end + 1])
    except json.JSONDecodeError:
        return None
    rate_limit = payload.get("rate_limit")
    if not isinstance(rate_limit, dict):
        return None
    windows = []
    details = rate_limit.get("details") if isinstance(rate_limit.get("details"), list) else []
    for item in details:
        if not isinstance(item, dict):
            continue
        used = _number(item.get("used_percent"))
        seconds = _int_number(item.get("limit_window_seconds"))
        reset = _epoch_seconds(item.get("reset_at"))
        if used is None:
            continue
        windows.append(
            UsageWindow(
                name=_codex_window_name("window", int(seconds / 60) if seconds else None),
                kind=_codex_window_kind(_codex_window_name("window", int(seconds / 60) if seconds else None)),
                used_percent=_clamp_percent(used),
                remaining_percent=_clamp_percent(100.0 - used),
                reset_at=reset,
                confidence=Confidence.OFFICIAL,
            )
        )
    if not windows:
        return None
    return CapacitySnapshot(
        provider_id=provider_id,
        status=_status_from_windows(windows),
        confidence=Confidence.OFFICIAL,
        windows=windows,
        raw={"source": "codex_app_server_error_body"},
    )


def _codex_window_name(fallback: str, duration_mins: int | None) -> str:
    if duration_mins == 300:
        return "5h"
    if duration_mins == 10080:
        return "weekly"
    return fallback


def _codex_window_kind(name: str) -> UsageWindowKind:
    if name == "5h":
        return UsageWindowKind.FIVE_HOUR
    if name == "weekly":
        return UsageWindowKind.WEEKLY
    return UsageWindowKind.UNKNOWN


def _safe_credit_summary(credits: dict[str, Any]) -> dict[str, Any]:
    return {
        "hasCredits": bool(credits.get("hasCredits")),
        "unlimited": bool(credits.get("unlimited")),
        "hasBalance": credits.get("balance") is not None,
    }
