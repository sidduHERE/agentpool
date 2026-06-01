from __future__ import annotations

from pathlib import Path
from typing import Any

PREFERENCES_PATH = Path("~/.agentpool/preferences.md").expanduser()
PREFERENCES_RESOURCE_URI = "agentpool://preferences.md"
PREFERENCES_READ_HINT = (
    "Read these user preferences before deciding whether to use AgentPool and before choosing provider/model."
)
DEFAULT_PREFERENCES_TEXT = """# AgentPool Preferences

Standing user preferences for agents using AgentPool.

These are guidance for agent judgment, not automatic routing rules. Read them
before deciding whether to use AgentPool and before choosing provider/model.

## How To Think About AgentPool

- Prefer your current harness's native subagent system when it can run the same
  provider/model more efficiently.
- Use AgentPool when you need another harness, another subscription, better
  remaining capacity, or a deliberately independent worker.
- Always choose provider and model explicitly.
- Never use `provider=auto`.

## Provider And Model Preferences

Edit these examples for your own subscriptions and pricing:

- Broad exploration / repo mapping / first-pass explanation: prefer the user's
  cheapest fast exploration harness, for example `cursor-cli` + `composer-2.5`.
- OpenAI-family models: prefer the user's OpenAI/Codex subscription, for
  example `codex-cli`, instead of spending another provider's credits.
- Claude-family models: prefer the user's Claude subscription, for example
  `claude-code`, unless the user asks otherwise.
- If a requested provider/model would spend the wrong subscription, pause and
  explain the tradeoff before spawning.

## Before Spawning

1. Check live or cached capacity with `agentpool usage-summary --json` or
   `get_usage_summary(refresh=false)`.
2. Check available models with `agentpool models --json` or
   `get_provider_models()`.
3. Apply these preferences manually.
4. Choose provider/model explicitly.
5. Keep the worker task narrow, then observe, steer, collect, and terminate
   deliberately.
"""


def ensure_preferences_file(path: Path | None = None, force: bool = False) -> dict[str, Any]:
    resolved = (path or PREFERENCES_PATH).expanduser()
    existed = resolved.exists()
    backup_path: Path | None = None
    if existed and not force:
        payload = preferences_payload(resolved, include_text=True)
        payload.update({"changed": False, "reason": "preferences already exist"})
        return payload
    if existed:
        backup_path = resolved.with_suffix(resolved.suffix + ".bak")
        backup_path.write_text(resolved.read_text(encoding="utf-8"), encoding="utf-8")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(DEFAULT_PREFERENCES_TEXT, encoding="utf-8")
    payload = preferences_payload(resolved, include_text=True)
    payload.update(
        {
            "changed": True,
            "backup_path": str(backup_path) if backup_path else None,
            "reason": "wrote default preferences",
        }
    )
    return payload


def read_preferences_text(path: Path | None = None) -> str:
    resolved = (path or PREFERENCES_PATH).expanduser()
    if resolved.exists():
        return resolved.read_text(encoding="utf-8")
    return DEFAULT_PREFERENCES_TEXT


def preferences_payload(path: Path | None = None, include_text: bool = True) -> dict[str, Any]:
    resolved = (path or PREFERENCES_PATH).expanduser()
    text = read_preferences_text(resolved)
    payload: dict[str, Any] = {
        "path": str(resolved),
        "resource_uri": PREFERENCES_RESOURCE_URI,
        "exists": resolved.exists(),
        "using_default_template": not resolved.exists(),
        "read_hint": PREFERENCES_READ_HINT,
        "digest": preference_digest(text),
    }
    if include_text:
        payload["text"] = text
    return payload


def preferences_reference(path: Path | None = None) -> dict[str, Any]:
    return preferences_payload(path, include_text=False)


def preference_digest(text: str, max_items: int = 8) -> list[str]:
    digest: list[str] = []
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                digest.append(current)
                current = None
            if len(digest) >= max_items:
                break
            continue
        if line.startswith("#"):
            continue
        if line.startswith("- "):
            if current:
                digest.append(current)
            line = line[2:].strip()
        elif line[0:2].isdigit() and ". " in line[:4]:
            if current:
                digest.append(current)
            line = line.split(". ", 1)[1].strip()
        elif current:
            current = f"{current} {line}"
            continue
        current = line
        if len(digest) >= max_items:
            break
    if current and len(digest) < max_items:
        digest.append(current)
    return digest
