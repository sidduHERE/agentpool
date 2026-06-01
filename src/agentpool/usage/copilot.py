from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from typing import Any

from agentpool.models import CapacitySnapshot, Confidence, UsageStatus, UsageWindow, UsageWindowKind
from agentpool.usage._common import (
    ProbeError,
    _clamp_percent,
    _clean_optional_string,
    _number,
    _parse_datetime,
    _request_json,
    _run_probe_command,
    _status_from_windows,
    unknown,
)


def copilot_cli_usage_snapshot(provider_id: str, binary: str | None = None) -> CapacitySnapshot:
    _ = binary
    token_result = _copilot_token()
    if not token_result:
        return CapacitySnapshot(
            provider_id=provider_id,
            status=UsageStatus.UNAUTHENTICATED,
            confidence=Confidence.UNKNOWN,
            warnings=[
                "No Copilot API token found. Set AGENTPOOL_COPILOT_TOKEN/GITHUB_TOKEN/GH_TOKEN, "
                "or authenticate gh so `gh auth token` can provide a token."
            ],
            raw={"source": "github_copilot_internal_api"},
        )
    token, token_source = token_result
    try:
        request = urllib.request.Request(
            "https://api.github.com/copilot_internal/user",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/json",
                # GitHub Copilot usage API client id; not an MCP host or VS Code IDE config.
                "Editor-Version": "vscode/1.96.2",
                "Editor-Plugin-Version": "copilot-chat/0.26.7",
                "User-Agent": "GitHubCopilotChat/0.26.7",
                "X-Github-Api-Version": "2025-04-01",
            },
        )
        payload = _request_json(request)
        snapshot = parse_copilot_usage_response(provider_id, payload)
        snapshot.raw["source"] = "github_copilot_internal_api"
        snapshot.raw["token_source"] = token_source
        return snapshot
    except ProbeError as exc:
        return unknown(
            provider_id,
            f"Copilot internal API probe failed: {exc}",
            source="github_copilot_internal_api",
        )


def parse_copilot_usage_response(provider_id: str, payload: dict[str, Any]) -> CapacitySnapshot:
    snapshots = payload.get("quota_snapshots")
    if not isinstance(snapshots, dict):
        snapshots = {}
    windows = []
    for name, item in (("premium_interactions", snapshots.get("premium_interactions")), ("chat", snapshots.get("chat"))):
        window = _copilot_window(name, item)
        if window:
            windows.append(window)
    if not windows:
        windows = _copilot_windows_from_legacy_counts(payload)
    if not windows:
        raise ProbeError("Copilot response did not include usable quota snapshots.")
    reset_at = _parse_datetime(payload.get("quota_reset_date"))
    if reset_at:
        windows = [window.model_copy(update={"reset_at": reset_at}) for window in windows]
    return CapacitySnapshot(
        provider_id=provider_id,
        status=_status_from_windows(windows),
        confidence=Confidence.OFFICIAL,
        windows=windows,
        reset_at=reset_at,
        raw={"plan": _clean_optional_string(payload.get("copilot_plan"))},
    )


def _copilot_token() -> tuple[str, str] | None:
    for key in ("AGENTPOOL_COPILOT_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(key)
        if value:
            return value, key
    gh = shutil.which("gh")
    if not gh:
        return None
    proc = _run_probe_command([gh, "auth", "token"], timeout=5)
    token = proc.stdout.strip()
    if proc.returncode == 0 and token:
        return token, "gh auth token"
    return None


def _copilot_window(name: str, item: object) -> UsageWindow | None:
    if not isinstance(item, dict):
        return None
    percent_remaining = _number(item.get("percent_remaining"))
    entitlement = _number(item.get("entitlement"))
    remaining = _number(item.get("remaining"))
    if percent_remaining is None and entitlement and remaining is not None:
        percent_remaining = (remaining / entitlement) * 100.0
    if percent_remaining is None or (entitlement == 0 and remaining == 0 and percent_remaining == 0):
        return None
    return UsageWindow(
        name=name,
        kind=UsageWindowKind.MONTHLY,
        used_percent=_clamp_percent(100.0 - percent_remaining),
        remaining_percent=_clamp_percent(percent_remaining),
        remaining_units=remaining,
        confidence=Confidence.OFFICIAL,
    )


def _copilot_windows_from_legacy_counts(payload: dict[str, Any]) -> list[UsageWindow]:
    monthly = payload.get("monthly_quotas")
    limited = payload.get("limited_user_quotas")
    if not isinstance(monthly, dict) or not isinstance(limited, dict):
        return []
    windows: list[UsageWindow] = []
    for name, key in (("premium_interactions", "completions"), ("chat", "chat")):
        total = _number(monthly.get(key))
        remaining = _number(limited.get(key))
        if not total or remaining is None:
            continue
        percent_remaining = (remaining / total) * 100.0
        windows.append(
            UsageWindow(
                name=name,
                kind=UsageWindowKind.MONTHLY,
                used_percent=_clamp_percent(100.0 - percent_remaining),
                remaining_percent=_clamp_percent(percent_remaining),
                remaining_units=remaining,
                confidence=Confidence.OFFICIAL,
            )
        )
    return windows
