from __future__ import annotations

import re

from agentpool.models import CapacitySnapshot, Confidence, UsageStatus, UsageWindow, UsageWindowKind


def parse_usage_warning(provider_id: str, text: str) -> CapacitySnapshot | None:
    if re.search(r"limit reached", text, re.I):
        return CapacitySnapshot(
            provider_id=provider_id,
            status=UsageStatus.LIMIT_REACHED,
            confidence=Confidence.PROVIDER_WARNING,
            windows=[
                UsageWindow(
                    name="unknown",
                    kind=UsageWindowKind.UNKNOWN,
                    status="limit_reached",
                    confidence=Confidence.PROVIDER_WARNING,
                    raw_text=text,
                )
            ],
            warnings=[text.strip()],
        )
    if re.search(r"approaching .*limit", text, re.I):
        return CapacitySnapshot(
            provider_id=provider_id,
            status=UsageStatus.NEAR_LIMIT,
            confidence=Confidence.PROVIDER_WARNING,
            windows=[
                UsageWindow(
                    name="unknown",
                    kind=UsageWindowKind.UNKNOWN,
                    status="near_limit",
                    confidence=Confidence.PROVIDER_WARNING,
                    raw_text=text,
                )
            ],
            warnings=[text.strip()],
        )
    return None
