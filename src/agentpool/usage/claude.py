from __future__ import annotations

import shutil

from agentpool.models import CapacitySnapshot
from agentpool.usage._common import _tmux_slash_usage_probe, unavailable
from agentpool.usage.provider_parsers import parse_claude_usage


def claude_code_usage_snapshot(provider_id: str, binary: str | None = None) -> CapacitySnapshot:
    executable = binary or shutil.which("claude")
    if not executable:
        return unavailable(provider_id, "Claude Code is not installed.")
    return _tmux_slash_usage_probe(
        provider_id=provider_id,
        command=[executable, "--allowed-tools", ""],
        slash_command="/usage",
        parser=parse_claude_usage,
        source="claude_pty_usage",
        startup_delay=1.2,
        timeout=18.0,
        extra_keys_after_match=[["PageDown"]],
    )
