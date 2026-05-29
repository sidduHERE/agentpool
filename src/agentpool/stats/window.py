from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from agentpool.models import ToolError

_DURATION_RE = re.compile(r"^(\d+)(h|d|w)$", re.I)


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    label: str
    spec: str


def parse_window(spec: str, now: datetime | None = None) -> Window:
    normalized = (spec or "").strip()
    if not normalized:
        raise ToolError("INVALID_WINDOW", "Window spec must not be empty.", {"spec": spec})

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    lowered = normalized.lower()
    if lowered == "all":
        return Window(
            start=datetime.fromtimestamp(0, tz=timezone.utc),
            end=current,
            label="all time",
            spec="all",
        )

    if "/" in normalized:
        start_text, end_text = normalized.split("/", 1)
        start = _parse_iso(start_text.strip())
        end = _parse_iso(end_text.strip())
        if end <= start:
            raise ToolError(
                "INVALID_WINDOW",
                "Window end must be after start.",
                {"spec": spec, "start": start.isoformat(), "end": end.isoformat()},
            )
        return Window(start=start, end=end, label=f"{start.date()} to {end.date()}", spec=normalized)

    duration = _DURATION_RE.match(lowered)
    if duration:
        amount = int(duration.group(1))
        unit = duration.group(2).lower()
        if unit == "h":
            delta = timedelta(hours=amount)
            label = f"last {amount}h"
        elif unit == "d":
            delta = timedelta(days=amount)
            label = f"last {amount}d"
        else:
            delta = timedelta(weeks=amount)
            label = f"last {amount}w"
        return Window(start=current - delta, end=current, label=label, spec=lowered)

    try:
        point = _parse_iso(normalized)
    except ToolError:
        raise ToolError("INVALID_WINDOW", f"Unrecognized window spec: {spec!r}.", {"spec": spec}) from None
    end = point + timedelta(days=1)
    return Window(start=point, end=min(end, current), label=str(point.date()), spec=normalized)


def _parse_iso(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise ToolError("INVALID_WINDOW", "ISO timestamp must not be empty.")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ToolError("INVALID_WINDOW", f"Invalid ISO timestamp: {value!r}.", {"value": value}) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
