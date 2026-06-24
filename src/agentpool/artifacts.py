from __future__ import annotations

from pathlib import Path
from typing import Any

from agentpool.event_detection import extract_result_body
from agentpool.git_worktree import changed_files, git_diff, git_status, is_git_repo
from agentpool.models import AgentSession, ArtifactRecord
from agentpool.redaction import redact_text
from agentpool.utils import repo_hash, sha256_file, write_json


PARTIAL_SUMMARY_MAX_CHARS = 4000


def create_artifact_dir(root: Path, repo_path: Path, session_id: str) -> Path:
    artifact_dir = root / repo_hash(repo_path) / session_id
    (artifact_dir / "raw" / "tmux-captures").mkdir(parents=True, exist_ok=True)
    (artifact_dir / "raw" / "terminal-control").mkdir(parents=True, exist_ok=True)
    return artifact_dir


def initialize_artifacts(session: AgentSession, prompt: str) -> None:
    artifact_dir = Path(session.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    Path(session.transcript_path).write_text("", encoding="utf-8")
    Path(session.events_path).write_text("", encoding="utf-8")
    (artifact_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    write_json(artifact_dir / "metadata.json", session.model_dump(mode="json"))


def append_transcript(session: AgentSession, text: str) -> None:
    path = Path(session.transcript_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_tail = _read_text_tail(path, max(len(text), 1)) if path.exists() else ""
    if text and text not in existing_tail:
        with path.open("a", encoding="utf-8") as fh:
            if path.exists() and path.stat().st_size > 0 and not existing_tail.endswith("\n"):
                fh.write("\n")
            fh.write(text)
            if not text.endswith("\n"):
                fh.write("\n")


def write_partial_summary(
    session: AgentSession,
    screen: str,
    *,
    state: str,
    event: str,
    readiness: str,
    observed_at: str,
) -> None:
    artifact_dir = Path(session.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    excerpt = screen.strip()[-PARTIAL_SUMMARY_MAX_CHARS:]
    if excerpt:
        excerpt = excerpt.replace("```", "'''")
    else:
        excerpt = "No visible worker output captured yet."
    text = (
        "# AgentPool Partial Summary\n\n"
        f"- session_id: `{session.id}`\n"
        f"- state: `{state}`\n"
        f"- event: `{event}`\n"
        f"- readiness: `{readiness}`\n"
        f"- observed_at: `{observed_at}`\n\n"
        "## Recent Worker Output\n\n"
        "```text\n"
        f"{excerpt}\n"
        "```\n"
    )
    (artifact_dir / "summary.partial.md").write_text(text, encoding="utf-8")


def collect_artifacts(session: AgentSession, screen: str, include_diff: bool = True) -> dict[str, Any]:
    artifact_dir = Path(session.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "latest_screen.txt").write_text(screen, encoding="utf-8")
    append_transcript(session, screen)
    workdir = Path(session.worktree_path or session.repo_path)
    status_text = git_status(workdir)
    diff_text = redact_text(git_diff(workdir)) if include_diff else ""
    (artifact_dir / "git-status.txt").write_text(status_text, encoding="utf-8")
    if include_diff:
        (artifact_dir / "diff.patch").write_text(diff_text, encoding="utf-8")
    summary = materialize_result_artifacts(session, screen) or "No AGENTPOOL result marker found."
    (artifact_dir / "summary.md").write_text(summary, encoding="utf-8")
    (artifact_dir / "result.md").write_text(summary, encoding="utf-8")
    write_json(
        artifact_dir / "metadata.json",
        {
            **session.model_dump(mode="json"),
            "git": {
                "is_repo": is_git_repo(workdir),
                "dirty": bool(status_text.strip()),
                "changed_files": changed_files(workdir) if is_git_repo(workdir) else [],
            },
        },
    )
    artifacts: list[ArtifactRecord] = []
    for kind, filename in [
        ("metadata", "metadata.json"),
        ("prompt", "prompt.md"),
        ("transcript", "transcript.txt"),
        ("events", "events.jsonl"),
        ("screen", "latest_screen.txt"),
        ("summary_partial", "summary.partial.md"),
        ("summary", "summary.md"),
        ("result", "result.md"),
        ("git_status", "git-status.txt"),
        ("diff", "diff.patch"),
    ]:
        path = artifact_dir / filename
        if path.exists():
            artifacts.append(ArtifactRecord(kind=kind, path=str(path), sha256=sha256_file(path)))
    return {
        "session_id": session.id,
        "state": session.state.value if hasattr(session.state, "value") else session.state,
        "artifact_dir": str(artifact_dir),
        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
        "summary": summary,
        "git": {
            "is_repo": is_git_repo(workdir),
            "dirty": bool(status_text.strip()),
            "changed_files": changed_files(workdir) if is_git_repo(workdir) else [],
        },
    }


def artifact_manifest(session: AgentSession, materialize_result: bool = True) -> dict[str, Any]:
    if materialize_result:
        materialize_result_artifacts(session)
    artifact_dir = Path(session.artifact_dir)
    files = []
    for kind, filename in [
        ("metadata", "metadata.json"),
        ("prompt", "prompt.md"),
        ("transcript", "transcript.txt"),
        ("events", "events.jsonl"),
        ("screen", "latest_screen.txt"),
        ("summary_partial", "summary.partial.md"),
        ("summary", "summary.md"),
        ("result", "result.md"),
        ("git_status", "git-status.txt"),
        ("diff", "diff.patch"),
    ]:
        path = artifact_dir / filename
        files.append(
            {
                "kind": kind,
                "path": str(path),
                "exists": path.exists(),
                "sha256": sha256_file(path) if path.exists() else None,
            }
        )
    return {
        "session_id": session.id,
        "artifact_dir": str(artifact_dir),
        "files": files,
    }


def materialize_result_artifacts(session: AgentSession, screen: str = "") -> str | None:
    artifact_dir = Path(session.artifact_dir)
    candidates = [screen]
    latest_screen = artifact_dir / "latest_screen.txt"
    if latest_screen.exists():
        candidates.append(latest_screen.read_text(encoding="utf-8"))
    transcript = Path(session.transcript_path)
    if transcript.exists():
        candidates.append(transcript.read_text(encoding="utf-8"))
    for candidate in candidates:
        summary = extract_result(candidate)
        if summary:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "summary.md").write_text(summary, encoding="utf-8")
            (artifact_dir / "result.md").write_text(summary, encoding="utf-8")
            return summary
    return None


def _read_text_tail(path: Path, max_chars: int) -> str:
    max_bytes = max(max_chars * 4, 4096)
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(-max_bytes, 2)
        data = fh.read()
    return data.decode("utf-8", errors="replace")[-max_chars:]


def extract_result(screen: str) -> str | None:
    body = extract_result_body(screen)
    if body:
        return body
    if "AGENTPOOL_SMOKE_DONE" in screen:
        return "AGENTPOOL_SMOKE_DONE"
    return None
