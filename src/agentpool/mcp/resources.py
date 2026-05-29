from __future__ import annotations

from importlib import resources
import json
from pathlib import Path
from typing import Any

from agentpool.agent_io import compact_artifact_manifest, lockdown_resource, omitted_worker_output, wrap_untrusted
from agentpool.session_manager import SessionManager

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def read_resource(manager: SessionManager, uri: str, lockdown: bool = False) -> str:
    if uri == "agentpool://quickstart":
        return _text_resource("agentpool-skill.md")
    if uri == "agentpool://onboarding":
        return _text_resource("onboarding.md")
    if uri == "agentpool://skill.md":
        return _text_resource("agentpool-skill.md")
    prefix = "agentpool://sessions/"
    if uri.startswith(prefix):
        tail = uri[len(prefix) :]
        parts = tail.split("/")
        session_id = parts[0]
        session = manager._require_session(session_id)
        if len(parts) == 1:
            return session.model_dump_json(indent=2)
        if parts[1] == "transcript":
            if lockdown:
                return _json(lockdown_resource(session.transcript_path, "transcript"))
            return _json(_worker_text_resource(session.id, "transcript", session.transcript_path))
        if parts[1] == "events":
            if lockdown:
                return _json(lockdown_resource(session.events_path, "events"))
            return _json(_worker_text_resource(session.id, "events", session.events_path))
    artifact_prefix = "agentpool://artifacts/"
    if uri.startswith(artifact_prefix):
        session_id = uri[len(artifact_prefix) :]
        return _json(compact_artifact_manifest(manager.artifact_manifest(session_id), lockdown=lockdown))
    raise KeyError(f"Unknown AgentPool resource URI: {uri}")


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _text_resource(filename: str) -> str:
    packaged = resources.files("agentpool").joinpath("docs", filename)
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    return (PROJECT_ROOT / "docs" / filename).read_text(encoding="utf-8")


def _worker_text_resource(session_id: str, kind: str, path: str | Path) -> dict[str, Any]:
    resolved = Path(path)
    text = resolved.read_text(encoding="utf-8") if resolved.exists() else ""
    return {
        "session_id": session_id,
        "kind": kind,
        "path": str(resolved),
        "exists": resolved.exists(),
        "worker_output": wrap_untrusted(text, "full") if text else omitted_worker_output("full"),
    }
