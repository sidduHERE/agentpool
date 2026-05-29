from __future__ import annotations

import shutil
import subprocess
from typing import Any

from agentpool.models import CapacitySnapshot, Confidence, UsageWindow, UsageWindowKind
from agentpool.usage._common import (
    ProbeError,
    _clamp_percent,
    _duration_window_kind,
    _extract_json_payload,
    _int_number,
    _number,
    _parse_datetime,
    _status_from_windows,
    _clean_optional_string,
    unavailable,
    unknown,
)

CODEXBAR_PROVIDER_MAP = {
    "claude-code": "claude",
    "codex-cli": "codex",
    "copilot-cli": "copilot",
    "cursor-cli": "cursor",
    "opencode": "opencode",
}

CODEXBAR_SAFE_SOURCE_MAP = {
    "claude-code": "cli",
    "codex-cli": "cli",
    "copilot-cli": "api",
    "cursor-cli": "cli",
    "opencode": "api",
}


def detect_codexbar(binary: str | None = None) -> dict[str, Any]:
    executable = binary or shutil.which("codexbar")
    if not executable:
        return {
            "installed": False,
            "path": None,
            "version": None,
            "supported_agentpool_providers": sorted(CODEXBAR_PROVIDER_MAP),
            "safe_sources": CODEXBAR_SAFE_SOURCE_MAP,
        }
    version = None
    try:
        proc = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=3, check=False)
        if proc.returncode == 0:
            version = (proc.stdout or proc.stderr).strip().splitlines()[0][:200]
    except (OSError, subprocess.TimeoutExpired):
        version = None
    return {
        "installed": True,
        "path": executable,
        "version": version,
        "supported_agentpool_providers": sorted(CODEXBAR_PROVIDER_MAP),
        "safe_sources": CODEXBAR_SAFE_SOURCE_MAP,
    }


def codexbar_usage_snapshot(
    provider_id: str,
    binary: str | None = None,
    source: str | None = None,
) -> CapacitySnapshot:
    executable = binary or shutil.which("codexbar")
    if not executable:
        return unavailable(provider_id, "CodexBar CLI is not installed.")
    codexbar_provider = CODEXBAR_PROVIDER_MAP.get(provider_id)
    if not codexbar_provider:
        return unknown(
            provider_id,
            f"CodexBar does not expose a safe mapped usage provider for {provider_id}.",
            source="codexbar",
        )
    safe_source = source or CODEXBAR_SAFE_SOURCE_MAP.get(provider_id)
    if not safe_source:
        return unknown(provider_id, f"No safe CodexBar source is configured for {provider_id}.", source="codexbar")
    command = [
        executable,
        "usage",
        "--provider",
        codexbar_provider,
        "--source",
        safe_source,
        "--format",
        "json",
        "--json-only",
        "--no-color",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return unknown(provider_id, f"CodexBar usage probe failed: {exc}", source="codexbar")
    text = "\n".join(part for part in [proc.stdout, proc.stderr] if part)
    try:
        payload = _extract_json_payload(text)
        snapshot = parse_codexbar_usage(provider_id, payload, expected_provider=codexbar_provider)
        snapshot.raw["source"] = "codexbar"
        snapshot.raw["codexbar_provider"] = codexbar_provider
        snapshot.raw["codexbar_source"] = safe_source
        if proc.returncode != 0:
            snapshot.warnings.append(f"CodexBar exited with status {proc.returncode}.")
        return snapshot
    except ProbeError as exc:
        return unknown(provider_id, f"CodexBar usage probe failed: {exc}", source="codexbar")


def parse_codexbar_usage(
    provider_id: str,
    payload: Any,
    expected_provider: str | None = None,
) -> CapacitySnapshot:
    entries = payload if isinstance(payload, list) else [payload]
    entry = None
    for item in entries:
        if not isinstance(item, dict):
            continue
        if expected_provider is None or item.get("provider") == expected_provider:
            entry = item
            break
    if entry is None:
        raise ProbeError("CodexBar output did not include the requested provider.")
    error = entry.get("error")
    if isinstance(error, dict):
        raise ProbeError(_clean_optional_string(error.get("message")) or str(error))
    usage = entry.get("usage")
    if not isinstance(usage, dict):
        raise ProbeError("CodexBar output did not include a usage object.")
    windows = []
    for name in ("primary", "secondary", "tertiary"):
        item = usage.get(name)
        if not isinstance(item, dict):
            continue
        used = _number(item.get("usedPercent"))
        remaining = _number(item.get("remainingPercent"))
        if used is None and remaining is None:
            continue
        if remaining is None and used is not None:
            remaining = 100.0 - used
        if used is None and remaining is not None:
            used = 100.0 - remaining
        duration = _int_number(item.get("windowMinutes"))
        windows.append(
            UsageWindow(
                name=_codexbar_window_name(name, duration),
                kind=_duration_window_kind(duration),
                status=name,
                used_percent=_clamp_percent(used) if used is not None else None,
                remaining_percent=_clamp_percent(remaining) if remaining is not None else None,
                reset_at=_parse_datetime(item.get("resetsAt")),
                confidence=Confidence.LOCAL_CLI,
                raw_text=f"codexbar:{name}:{duration or 'unknown'}",
            )
        )
    credits_remaining = None
    credits = entry.get("credits")
    if isinstance(credits, dict):
        credits_remaining = _number(credits.get("remaining"))
    if not windows and credits_remaining is None:
        raise ProbeError("CodexBar output did not include parseable windows or credits.")
    raw: dict[str, Any] = {
        "codexbar_source": entry.get("source"),
        "version": entry.get("version"),
        "login_method": _clean_optional_string(usage.get("loginMethod")),
        "account_email": _clean_optional_string(usage.get("accountEmail")),
    }
    return CapacitySnapshot(
        provider_id=provider_id,
        status=_status_from_windows(windows),
        confidence=Confidence.LOCAL_CLI,
        windows=windows,
        credits_remaining=credits_remaining,
        raw={key: value for key, value in raw.items() if value is not None},
    )


def _codexbar_window_name(fallback: str, duration_mins: int | None) -> str:
    kind = _duration_window_kind(duration_mins)
    if kind != UsageWindowKind.UNKNOWN:
        return kind.value
    return fallback
