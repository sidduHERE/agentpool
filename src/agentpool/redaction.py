from __future__ import annotations

import re


SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization:\s*[A-Za-z][A-Za-z0-9._+-]*\s+)[^\s]+"),
    re.compile(r"(?i)((?:api|access|refresh|secret|private|session|auth)?_?(?:token|key|secret|password)=)[^\s]+"),
    re.compile(r"(?i)((?:api|access|refresh|secret|private|session|auth)?[-_ ]?(?:token|key|secret|password):\s*)[^\s]+"),
    re.compile(r"(?i)((?:postgres|postgresql|mysql|mongodb|redis)://[^:\s]+:)[^@\s]+(@)"),
    re.compile(r"(?i)(sk-[A-Za-z0-9_\-]{16,})"),
    re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]


def redact_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]{match.group(2)}", redacted)
        elif pattern.groups == 1:
            redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted
