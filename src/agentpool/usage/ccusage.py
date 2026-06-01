from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from typing import Any

from agentpool.models import CapacitySnapshot, Confidence, UsageStatus, UsageWindow, UsageWindowKind
from agentpool.usage._common import (
    ProbeError,
    _clamp_percent,
    _extract_json_payload,
    _number,
    _parse_datetime,
    _run_probe_command,
    unavailable,
    unknown,
)


def detect_ccusage(binary: str | None = None) -> dict[str, Any]:
    command = _ccusage_command(binary)
    if not command:
        return {"installed": False, "path": None, "version": None, "safe_source": "local_claude_code_logs"}
    version = None
    try:
        proc = _run_probe_command([*command, "--version"], timeout=5)
        if proc.returncode == 0:
            version = (proc.stdout or proc.stderr).strip().splitlines()[0][:200]
    except (OSError, subprocess.TimeoutExpired):
        version = None
    return {
        "installed": True,
        "path": command[0],
        "command": command,
        "version": version,
        "safe_source": "local_claude_code_logs",
    }


def ccusage_usage_snapshot(provider_id: str, binary: str | None = None) -> CapacitySnapshot:
    if provider_id != "claude-code":
        return unknown(provider_id, f"ccusage is only mapped for claude-code, not {provider_id}.", source="ccusage")
    command = _ccusage_command(binary)
    if not command:
        return unavailable(
            provider_id,
            "ccusage CLI is not installed. Set AGENTPOOL_CCUSAGE_COMMAND to an explicit command if desired.",
        )
    try:
        proc = _run_probe_command([*command, "blocks", "--json", "--offline", "--active", "--no-color"], timeout=45)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return unknown(provider_id, f"ccusage probe failed: {exc}", source="ccusage")
    text = "\n".join(part for part in [proc.stdout, proc.stderr] if part)
    try:
        payload = _extract_json_payload(text)
        snapshot = parse_ccusage_blocks(provider_id, payload)
        if proc.returncode != 0:
            snapshot.warnings.append(f"ccusage exited with status {proc.returncode}.")
        return snapshot
    except ProbeError as exc:
        return unknown(provider_id, f"ccusage probe failed: {exc}", source="ccusage")


def parse_ccusage_blocks(provider_id: str, payload: Any) -> CapacitySnapshot:
    if not isinstance(payload, dict):
        raise ProbeError("ccusage output must be a JSON object.")
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        raise ProbeError("ccusage output did not include blocks.")
    active = None
    for block in blocks:
        if isinstance(block, dict) and block.get("isActive") is True and not block.get("isGap"):
            active = block
    if active is None:
        raise ProbeError("ccusage output did not include an active usage block.")
    projection = active.get("projection") if isinstance(active.get("projection"), dict) else {}
    burn_rate = active.get("burnRate") if isinstance(active.get("burnRate"), dict) else {}
    token_counts = active.get("tokenCounts") if isinstance(active.get("tokenCounts"), dict) else {}
    token_limit_status = active.get("tokenLimitStatus") if isinstance(active.get("tokenLimitStatus"), dict) else {}
    token_limit_used = _number(token_limit_status.get("percentUsed"))
    window = UsageWindow(
        name="active_block",
        kind=UsageWindowKind.FIVE_HOUR,
        status="active_local_log_block",
        used_percent=_clamp_percent(token_limit_used) if token_limit_used is not None else None,
        used_units=_number(active.get("totalTokens")),
        reset_at=_parse_datetime(active.get("endTime")),
        confidence=Confidence.OBSERVED,
        raw_text="ccusage:blocks:active",
    )
    raw = {
        "source": "ccusage_local_logs",
        "block_id": active.get("id"),
        "start_time": active.get("startTime"),
        "actual_end_time": active.get("actualEndTime"),
        "entries": active.get("entries"),
        "cost_usd": _number(active.get("costUSD")),
        "models": active.get("models") if isinstance(active.get("models"), list) else [],
        "token_counts": token_counts,
        "burn_rate": burn_rate,
        "projection": projection,
        "token_limit_status": token_limit_status,
    }
    return CapacitySnapshot(
        provider_id=provider_id,
        status=UsageStatus.UNKNOWN,
        confidence=Confidence.OBSERVED,
        windows=[window],
        warnings=["ccusage reads local Claude Code logs; it is telemetry, not authoritative quota."],
        raw=raw,
    )


def _ccusage_command(binary: str | None = None) -> list[str] | None:
    if binary:
        return shlex.split(binary)
    configured = os.environ.get("AGENTPOOL_CCUSAGE_COMMAND")
    if configured:
        return shlex.split(configured)
    executable = shutil.which("ccusage")
    if executable:
        return [executable]
    return None
