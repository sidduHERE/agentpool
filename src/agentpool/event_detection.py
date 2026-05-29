from __future__ import annotations

import hashlib
import re
import textwrap
from dataclasses import dataclass

from agentpool.models import Confidence, ObserveEvent, SessionState


QUESTION_PATTERNS = [
    re.compile(r"Should I\b", re.I),
    re.compile(r"Do you want\b", re.I),
    re.compile(r"Which .* should I\b", re.I),
    re.compile(r"^[^\n]{0,240}\?\s*$", re.I | re.M),
]
APPROVAL_PATTERNS = [
    re.compile(r"Proceed\?", re.I),
    re.compile(r"Continue\?", re.I),
    re.compile(r"Do you trust\b", re.I),
    re.compile(r"Hooks need review", re.I),
    re.compile(r"Update available!", re.I),
    re.compile(r"Allow .*\?", re.I),
    re.compile(r"\[y/N\]", re.I),
    re.compile(r"\(y/n\)", re.I),
]
OVERAGE_PATTERNS = [
    re.compile(r"extra usage", re.I),
    re.compile(r"overage", re.I),
    re.compile(r"continue with paid", re.I),
    re.compile(r"API credits", re.I),
]
LIMIT_PATTERNS = [
    re.compile(r"approaching .*limit", re.I),
    re.compile(r"limit reached", re.I),
    re.compile(r"resets", re.I),
]
ERROR_PATTERNS = [
    re.compile(r"command not found", re.I),
    re.compile(r"not authenticated", re.I),
    re.compile(r"login required", re.I),
    re.compile(r"authentication required", re.I),
    re.compile(r"press any key to log in", re.I),
    re.compile(r"access denied.*invalid subscription", re.I | re.S),
    re.compile(r"wrong API endpoint", re.I),
    re.compile(r"unable to reach localhost", re.I),
    re.compile(r"\btraceback\b", re.I),
    re.compile(r"\bexception\b", re.I),
    re.compile(r"\bpanic(?:ked)?\b", re.I),
]
DONE_PATTERN = re.compile(
    r"^\s*(?:[•●-]\s*)?AGENTPOOL_RESULT_START\s*$(?P<body>.*?)"
    r"^\s*(?:[•●-]\s*)?AGENTPOOL_RESULT_END\s*$",
    re.I | re.M | re.S,
)
SMOKE_DONE_PATTERN = re.compile(r"\bAGENTPOOL_SMOKE_DONE\b", re.I)


@dataclass(frozen=True)
class Detection:
    state: SessionState
    event: ObserveEvent
    confidence: Confidence
    parsed_question: str | None = None


def screen_hash(screen: str) -> str:
    return hashlib.sha256(screen.encode("utf-8")).hexdigest()


def trim_excerpt(screen: str, max_chars: int = 4000) -> str:
    return screen.strip()[-max_chars:]


def detect_event(screen: str, previous_hash: str | None = None) -> Detection:
    excerpt = trim_excerpt(screen)
    active_excerpt = trim_excerpt(screen, max_chars=1200)
    approval_excerpt = _active_prompt_region(active_excerpt)
    if SMOKE_DONE_PATTERN.search(_strip_prompt_lines(active_excerpt)):
        return Detection(SessionState.COMPLETED, ObserveEvent.COMPLETED, Confidence.OBSERVED)
    if extract_result_body(excerpt):
        return Detection(SessionState.COMPLETED, ObserveEvent.COMPLETED, Confidence.OBSERVED)
    for pattern in OVERAGE_PATTERNS:
        if pattern.search(approval_excerpt):
            return Detection(SessionState.AWAITING_APPROVAL, ObserveEvent.OVERAGE_PROMPT, Confidence.OBSERVED)
    for pattern in LIMIT_PATTERNS:
        if pattern.search(active_excerpt):
            return Detection(SessionState.RUNNING, ObserveEvent.LIMIT_WARNING, Confidence.OBSERVED)
    for pattern in APPROVAL_PATTERNS:
        if pattern.search(approval_excerpt):
            return Detection(SessionState.AWAITING_APPROVAL, ObserveEvent.APPROVAL_PROMPT, Confidence.OBSERVED)
    for pattern in QUESTION_PATTERNS:
        match = pattern.search(active_excerpt)
        if match:
            line = _line_for_match(active_excerpt, match.start())
            return Detection(SessionState.AWAITING_USER_INPUT, ObserveEvent.QUESTION, Confidence.OBSERVED, line)
    for pattern in ERROR_PATTERNS:
        if pattern.search(active_excerpt):
            return Detection(SessionState.FAILED, ObserveEvent.ERROR, Confidence.OBSERVED)
    current_hash = screen_hash(screen)
    if previous_hash and previous_hash != current_hash:
        return Detection(SessionState.RUNNING, ObserveEvent.SCREEN_CHANGED, Confidence.OBSERVED)
    return Detection(SessionState.RUNNING, ObserveEvent.NONE, Confidence.UNKNOWN)


def _line_for_match(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def extract_result_body(screen: str) -> str | None:
    for match in DONE_PATTERN.finditer(trim_excerpt(screen)):
        body = match.group("body")
        if _looks_like_actual_result(body):
            return textwrap.dedent(body).strip()
    return None


def _looks_like_actual_result(body: str) -> bool:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return False
    for index, line in enumerate(lines):
        if line.lower().startswith("summary"):
            after_colon = line.split(":", 1)[1].strip() if ":" in line else ""
            if after_colon:
                return True
            next_line = lines[index + 1].lower() if index + 1 < len(lines) else ""
            return bool(next_line and not next_line.startswith("findings"))
    return True


def _strip_prompt_lines(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("❯", "›", ">")):
            continue
        lines.append(line)
    return "\n".join(lines)


def _active_prompt_region(text: str) -> str:
    matches = list(re.finditer(r"(?m)^›\s+(?!\d+[.)]?\s)", text))
    if not matches:
        return text
    return text[matches[-1].start() :]
