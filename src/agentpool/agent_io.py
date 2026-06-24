from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any, Literal

from agentpool.models import ToolError

Detail = Literal["summary", "excerpt", "full"]

DETAILS: set[str] = {"summary", "excerpt", "full"}
EXCERPT_CHARS = 1600
FULL_CHARS = 8000
RAW_ARTIFACT_KINDS = {"transcript", "events", "screen", "summary_partial", "summary", "result", "diff"}


def parse_detail(value: str) -> Detail:
    normalized = value.strip().lower()
    if normalized in DETAILS:
        return normalized  # type: ignore[return-value]
    raise ToolError(
        "INVALID_DETAIL",
        "Detail must be summary, excerpt, or full.",
        {"detail": value, "example": "--detail excerpt"},
    )


def read_stdin_text(stdin_text: str, label: str, example: str) -> str:
    text = stdin_text.strip()
    if text:
        return text
    raise ToolError(
        "INVALID_STDIN",
        f"No {label} was provided on stdin.",
        {"example": example},
    )


def wrap_untrusted(text: str, detail: Detail) -> dict[str, Any]:
    limit = EXCERPT_CHARS if detail == "excerpt" else FULL_CHARS
    token = secrets.token_hex(8)
    begin = f"BEGIN_UNTRUSTED_WORKER_OUTPUT_{token}"
    end = f"END_UNTRUSTED_WORKER_OUTPUT_{token}"
    clipped = tail_text(text, limit)
    escaped = clipped.replace(begin, f"ESCAPED_{begin}").replace(end, f"ESCAPED_{end}")
    return {
        "included": True,
        "detail": detail,
        "truncated": len(text.strip()) > len(clipped),
        "chars": len(escaped),
        "text": f"{begin}\n{escaped}\n{end}",
    }


def omitted_worker_output(detail: Detail, lockdown: bool = False) -> dict[str, Any]:
    reason = "lockdown" if lockdown else f"detail={detail}"
    return {"included": False, "detail": detail, "reason": reason}


def tail_text(text: str, limit: int) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[-limit:]


def compact_artifact_manifest(manifest: dict[str, Any], lockdown: bool = False) -> dict[str, Any]:
    files = []
    for file in manifest.get("files") or []:
        files.append(gate_raw_artifact(file, lockdown))
    return {
        "session_id": manifest.get("session_id"),
        "artifact_dir": manifest.get("artifact_dir"),
        "files": files,
    }


def observe_payload(
    response: dict[str, Any],
    artifact_manifest: dict[str, Any],
    detail: Detail,
    lockdown: bool = False,
) -> dict[str, Any]:
    payload = {
        "session_id": response.get("session_id"),
        "state": response.get("state"),
        "event": response.get("event"),
        "confidence": response.get("confidence"),
        "metadata": response.get("metadata") or {},
        "artifact_manifest": compact_artifact_manifest(artifact_manifest, lockdown=lockdown),
    }
    if response.get("parsed_question") and detail != "summary" and not lockdown:
        payload["parsed_question"] = wrap_untrusted(str(response["parsed_question"]), "excerpt")
    else:
        payload["parsed_question_available"] = bool(response.get("parsed_question"))
    text = response.get("screen_excerpt") or response.get("recent_log") or ""
    if detail == "summary" or lockdown or not text:
        payload["worker_output"] = omitted_worker_output(detail, lockdown)
    else:
        payload["worker_output"] = wrap_untrusted(str(text), detail)
    return payload


def collect_payload(result: dict[str, Any], detail: Detail, lockdown: bool = False) -> dict[str, Any]:
    payload = {
        "session_id": result.get("session_id"),
        "state": result.get("state"),
        "artifact_dir": result.get("artifact_dir"),
        "artifacts": [gate_raw_artifact(artifact, lockdown) for artifact in result.get("artifacts") or []],
        "git": result.get("git"),
    }
    if result.get("warnings"):
        payload["warnings"] = result["warnings"]
    summary = str(result.get("summary") or "")
    if detail == "summary" or lockdown or not summary:
        payload["worker_output"] = omitted_worker_output(detail, lockdown)
    else:
        payload["worker_output"] = wrap_untrusted(summary, detail)
    return payload


def lockdown_resource(path: str | Path, kind: str) -> dict[str, Any]:
    return {
        "blocked": True,
        "reason": "lockdown",
        "kind": kind,
        "path": str(path),
    }


def gate_raw_artifact(artifact: dict[str, Any], lockdown: bool) -> dict[str, Any]:
    row = dict(artifact)
    if lockdown and row.get("kind") in RAW_ARTIFACT_KINDS:
        row["gated"] = True
        row["gated_reason"] = "lockdown"
    return row
