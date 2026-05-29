from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from agentpool.models import CapacitySnapshot, Confidence, UsageStatus, UsageWindow, UsageWindowKind


def parse_codex_status(provider_id: str, text: str) -> CapacitySnapshot | None:
    windows: list[UsageWindow] = []
    for name, percent, reset in re.findall(
        r"(?P<name>5h|Weekly)\s+limit:\s+\[[^\]]+\]\s+(?P<percent>\d+(?:\.\d+)?)%\s+left\s+\(resets\s+(?P<reset>[^)]+)\)",
        text,
        re.I,
    ):
        windows.append(
            UsageWindow(
                name=name.lower(),
                kind=UsageWindowKind.FIVE_HOUR if name.lower() == "5h" else UsageWindowKind.WEEKLY,
                remaining_percent=float(percent),
                used_percent=100.0 - float(percent),
                confidence=Confidence.LOCAL_CLI,
                raw_text=f"{name} limit resets {reset}",
            )
        )
    footer = re.search(
        r"\b5h\s+(?P<hour>\d+(?:\.\d+)?)%\s+.*?\bweekly\s+(?P<weekly>\d+(?:\.\d+)?)%",
        text,
        re.I | re.S,
    )
    if footer and not windows:
        windows.extend(
            [
                UsageWindow(
                    name="5h",
                    kind=UsageWindowKind.FIVE_HOUR,
                    remaining_percent=float(footer.group("hour")),
                    used_percent=100.0 - float(footer.group("hour")),
                    confidence=Confidence.LOCAL_CLI,
                    raw_text=footer.group(0),
                ),
                UsageWindow(
                    name="weekly",
                    kind=UsageWindowKind.WEEKLY,
                    remaining_percent=float(footer.group("weekly")),
                    used_percent=100.0 - float(footer.group("weekly")),
                    confidence=Confidence.LOCAL_CLI,
                    raw_text=footer.group(0),
                ),
            ]
        )
    if not windows:
        return None
    status = _status_from_remaining(min(w.remaining_percent or 100.0 for w in windows))
    return CapacitySnapshot(provider_id=provider_id, status=status, confidence=Confidence.LOCAL_CLI, windows=windows)



def parse_claude_usage(provider_id: str, text: str) -> CapacitySnapshot | None:
    clean = _strip_ansi(text)
    if re.search(r"failed to load usage|token expired|not authenticated|login required", clean, re.I):
        return CapacitySnapshot(
            provider_id=provider_id,
            status=UsageStatus.UNAUTHENTICATED,
            confidence=Confidence.LOCAL_CLI,
            warnings=["Claude CLI reported that usage could not be loaded."],
            raw={"source": "claude_cli_usage_error"},
        )
    windows: list[UsageWindow] = []
    for label, name in (
        ("Current session", "session"),
        ("Current week (all models)", "weekly"),
        ("Current week (Opus)", "weekly_opus"),
        ("Current week (Sonnet only)", "weekly_sonnet"),
        ("Current week (Sonnet)", "weekly_sonnet"),
        ("Current week", "weekly"),
    ):
        window = _extract_labeled_percent(clean, label, name)
        if window and all(existing.name != window.name for existing in windows):
            windows.append(window)
    if not windows:
        return None
    credits_remaining = None
    extra = re.search(
        r"Extra usage(?P<context>.{0,260}?\$(?P<spent>\d+(?:\.\d+)?)\s*/\s*\$(?P<limit>\d+(?:\.\d+)?)\s+spent)",
        clean,
        re.I | re.S,
    )
    if extra:
        spent = float(extra.group("spent"))
        limit = float(extra.group("limit"))
        remaining = max(0.0, limit - spent)
        credits_remaining = round(remaining, 2)
        used_percent = (spent / limit) * 100 if limit > 0 else 100.0
        windows.append(
            UsageWindow(
                name="extra_usage",
                kind=UsageWindowKind.ON_DEMAND,
                used_percent=max(0.0, min(100.0, used_percent)),
                remaining_percent=max(0.0, min(100.0, 100.0 - used_percent)),
                used_units=spent,
                remaining_units=credits_remaining,
                confidence=Confidence.LOCAL_CLI,
                raw_text=extra.group("context").strip(),
            )
        )
    status = _status_from_remaining(min(w.remaining_percent or 100.0 for w in windows))
    return CapacitySnapshot(
        provider_id=provider_id,
        status=status,
        confidence=Confidence.LOCAL_CLI,
        windows=windows,
        credits_remaining=credits_remaining,
    )


def parse_devin_usage(provider_id: str, text: str) -> CapacitySnapshot | None:
    match = re.search(r"Quota used:\s+(?P<used>\d+(?:\.\d+)?)%\s+\(remaining:\s+(?P<remaining>\d+(?:\.\d+)?)%\)", text, re.I)
    if not match:
        banner = re.search(r"\b(?P<remaining>\d+(?:\.\d+)?)%\s+remaining\s+\(resets\s+in\s+(?P<reset>[^)]+)\)", text, re.I)
        if not banner:
            return None
        remaining = float(banner.group("remaining"))
        used = 100.0 - remaining
        raw = banner.group(0)
    else:
        used = float(match.group("used"))
        remaining = float(match.group("remaining"))
        raw = match.group(0)
    credits = None
    credits_match = re.search(r"Extra usage balance:\s+\$(?P<credits>\d+(?:\.\d+)?)", text, re.I)
    if credits_match:
        credits = float(credits_match.group("credits"))
    reset_at = _parse_devin_reset(text)
    return CapacitySnapshot(
        provider_id=provider_id,
        status=_status_from_remaining(remaining),
        confidence=Confidence.LOCAL_CLI,
        credits_remaining=credits,
        warnings=["Devin CLI /usage exposes the weekly included quota; daily quota requires the plan-status API."],
        windows=[
            UsageWindow(
                name="weekly",
                kind=UsageWindowKind.WEEKLY,
                status="weekly",
                used_percent=used,
                remaining_percent=remaining,
                reset_at=reset_at,
                confidence=Confidence.LOCAL_CLI,
                raw_text=raw,
            )
        ],
    )


def parse_droid_status(provider_id: str, text: str) -> CapacitySnapshot | None:
    if "Credit Usage (Current Session)" not in text and "Session Token Usage" not in text:
        return None
    raw: dict[str, float] = {}
    for key, value in re.findall(r"(Input|Output|Cache Creation|Cache Read):\s+([\d.]+)\s+(?:credits|tokens)", text, re.I):
        raw[key.lower().replace(" ", "_")] = float(value)
    return CapacitySnapshot(
        provider_id=provider_id,
        status=UsageStatus.UNKNOWN,
        confidence=Confidence.LOCAL_CLI,
        warnings=["Droid CLI status exposes current-session usage, not subscription quota."],
        raw={"current_session": raw},
    )


def parse_opencode_stats(provider_id: str, text: str) -> CapacitySnapshot | None:
    if "COST & TOKENS" not in text and "MODEL USAGE" not in text:
        return None
    raw: dict[str, float | int] = {}
    money = re.search(r"Total Cost\s+\$(?P<cost>\d+(?:\.\d+)?)", text, re.I)
    messages = re.search(r"Messages\s+(?P<messages>[\d,]+)", text, re.I)
    sessions = re.search(r"Sessions\s+(?P<sessions>[\d,]+)", text, re.I)
    if money:
        raw["total_cost"] = float(money.group("cost"))
    if messages:
        raw["messages"] = int(messages.group("messages").replace(",", ""))
    if sessions:
        raw["sessions"] = int(sessions.group("sessions").replace(",", ""))
    return CapacitySnapshot(
        provider_id=provider_id,
        status=UsageStatus.UNKNOWN,
        confidence=Confidence.LOCAL_CLI,
        warnings=["OpenCode stats expose local token/cost history, not subscription quota."],
        raw=raw,
    )


def _status_from_remaining(remaining: float) -> UsageStatus:
    if remaining <= 0:
        return UsageStatus.LIMIT_REACHED
    if remaining <= 15:
        return UsageStatus.NEAR_LIMIT
    return UsageStatus.AVAILABLE


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def _parse_devin_reset(text: str) -> datetime | None:
    match = re.search(
        r"Quota resets\s+(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2}),\s+(?P<time>\d{1,2}:\d{2}\s+[AP]M)\s+\(UTC(?P<offset>[+-]\d{1,2})\)",
        text,
    )
    if not match:
        return None
    year = datetime.now().year
    offset = timezone(timedelta(hours=int(match.group("offset"))))
    try:
        parsed = datetime.strptime(
            f"{match.group('month')} {match.group('day')} {year} {match.group('time')}",
            "%B %d %Y %I:%M %p",
        )
    except ValueError:
        return None
    reset_at = parsed.replace(tzinfo=offset)
    if reset_at < datetime.now(offset) - timedelta(days=180):
        reset_at = reset_at.replace(year=year + 1)
    return reset_at


def _extract_labeled_percent(text: str, label: str, name: str) -> UsageWindow | None:
    pattern = re.compile(
        rf"{re.escape(label)}(?P<context>.{{0,800}}?)(?=(?:Current session|Current week|\Z))",
        re.I | re.S,
    )
    match = pattern.search(text)
    if not match:
        return None
    context = match.group("context")
    percent_match = re.search(
        r"(?P<percent>\d+(?:\.\d+)?)%\s*(?P<direction>left|remaining|used)?",
        context,
        re.I,
    )
    if not percent_match:
        return None
    percent = float(percent_match.group("percent"))
    direction = (percent_match.group("direction") or "remaining").lower()
    if direction == "used":
        used = percent
        remaining = 100.0 - percent
    else:
        remaining = percent
        used = 100.0 - percent
    reset_match = re.search(r"resets?\s+(?:in\s+)?(?P<reset>[^\n\r|·]+)", context, re.I)
    raw = f"{label}{context[:400]}"
    if reset_match:
        raw = f"{raw} reset {reset_match.group('reset').strip()}"
    return UsageWindow(
        name=name,
        kind=_kind_from_name(name),
        used_percent=max(0.0, min(100.0, used)),
        remaining_percent=max(0.0, min(100.0, remaining)),
        confidence=Confidence.LOCAL_CLI,
        raw_text=raw.strip(),
    )


def _kind_from_name(name: str) -> UsageWindowKind:
    if name == "session":
        return UsageWindowKind.SESSION
    if name == "weekly" or name.startswith("weekly_"):
        return UsageWindowKind.WEEKLY
    if name == "daily":
        return UsageWindowKind.DAILY
    if name == "5h":
        return UsageWindowKind.FIVE_HOUR
    if name in {"extra_usage", "on_demand"}:
        return UsageWindowKind.ON_DEMAND
    return UsageWindowKind.UNKNOWN
