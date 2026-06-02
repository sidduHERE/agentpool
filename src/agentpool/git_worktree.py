from __future__ import annotations
from pathlib import Path

from agentpool.models import ToolError
from agentpool.utils import run_capture


def is_git_repo(path: Path) -> bool:
    proc = run_capture(["git", "rev-parse", "--is-inside-work-tree"], cwd=path)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def git_status(path: Path) -> str:
    if not is_git_repo(path):
        return ""
    return run_capture(["git", "status", "--porcelain"], cwd=path).stdout


def git_diff(path: Path) -> str:
    if not is_git_repo(path):
        return ""
    proc = run_capture(["git", "diff", "--binary"], cwd=path, timeout=30)
    if proc.returncode == 124:
        return "[agentpool] git diff timed out after 30s; diff omitted.\n"
    if proc.returncode != 0:
        return f"[agentpool] git diff failed: {proc.stderr.strip()}\n"
    return proc.stdout


def changed_files(path: Path) -> list[str]:
    status = git_status(path)
    return [line[3:] for line in status.splitlines() if len(line) > 3]


def create_worktree(repo_path: Path, provider_id: str, session_id: str) -> Path:
    if not is_git_repo(repo_path):
        raise ToolError("GIT_NOT_REPO", "Worktree isolation requires a git repository.", {"repo_path": str(repo_path)})
    parent = repo_path.parent / ".agentpool-worktrees"
    parent.mkdir(parents=True, exist_ok=True)
    worktree_path = parent / session_id
    branch = agentpool_branch(provider_id, session_id)
    proc = run_capture(
        ["git", "worktree", "add", "-b", branch, str(worktree_path)],
        cwd=repo_path,
    )
    if proc.returncode != 0:
        raise ToolError(
            "WORKTREE_FAILED",
            "Failed to create git worktree.",
            {"repo_path": str(repo_path), "stderr": proc.stderr, "branch": branch},
        )
    return worktree_path


def agentpool_branch(provider_id: str, session_id: str) -> str:
    safe_provider = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in provider_id).strip("-")
    return f"agentpool/{safe_provider}/{session_id[-6:]}"


def delete_agentpool_branch(repo_path: Path, provider_id: str, session_id: str) -> dict[str, str | bool]:
    if not is_git_repo(repo_path):
        return {"deleted": False, "reason": "not_git_repo"}
    branch = agentpool_branch(provider_id, session_id)
    proc = run_capture(
        ["git", "branch", "-D", branch],
        cwd=repo_path,
    )
    if proc.returncode != 0:
        return {"deleted": False, "branch": branch, "stderr": proc.stderr.strip()}
    return {"deleted": True, "branch": branch}


def list_agentpool_worktrees(repo_path: Path) -> list[dict[str, str | bool]]:
    if not is_git_repo(repo_path):
        return []
    proc = run_capture(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_path,
    )
    if proc.returncode != 0:
        return []
    worktrees: list[dict[str, str | bool]] = []
    current: dict[str, str | bool] = {}
    for line in proc.stdout.splitlines():
        if not line:
            if _is_agentpool_worktree(current):
                worktrees.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "branch":
            current["branch"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "bare":
            current["bare"] = True
    if _is_agentpool_worktree(current):
        worktrees.append(current)
    return worktrees


def plan_cleanup_worktree(repo_path: Path, worktree_path: Path, force: bool = False) -> dict[str, str | bool]:
    if not is_git_repo(repo_path):
        raise ToolError("GIT_NOT_REPO", "Worktree cleanup requires a git repository.", {"repo_path": str(repo_path)})
    if not worktree_path.exists():
        return {
            "dry_run": True,
            "would_remove": False,
            "blocked": False,
            "path": str(worktree_path),
            "reason": "missing",
        }
    dirty = bool(git_status(worktree_path).strip())
    blocked = dirty and not force
    return {
        "dry_run": True,
        "would_remove": not blocked,
        "blocked": blocked,
        "path": str(worktree_path),
        "dirty": dirty,
        "force": force,
        "reason": "dirty" if blocked else None,
    }


def cleanup_worktree(repo_path: Path, worktree_path: Path, force: bool = False) -> dict[str, str | bool]:
    plan = plan_cleanup_worktree(repo_path, worktree_path, force=force)
    if plan.get("reason") == "missing":
        return {"removed": False, "path": str(worktree_path), "reason": str(plan.get("reason") or "missing")}
    dirty = bool(plan.get("dirty"))
    if dirty and not force:
        raise ToolError(
            "WORKTREE_DIRTY",
            "Worktree has uncommitted changes; pass force to remove it.",
            {"worktree_path": str(worktree_path)},
        )
    args = ["git", "worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path))
    proc = run_capture(args, cwd=repo_path)
    if proc.returncode != 0:
        raise ToolError(
            "WORKTREE_CLEANUP_FAILED",
            "Failed to remove git worktree.",
            {"worktree_path": str(worktree_path), "stderr": proc.stderr},
        )
    return {"removed": True, "path": str(worktree_path), "dirty": dirty}


def _is_agentpool_worktree(entry: dict[str, str | bool]) -> bool:
    path = str(entry.get("path") or "")
    branch = str(entry.get("branch") or "")
    return ".agentpool-worktrees" in path or branch.startswith("refs/heads/agentpool/")
