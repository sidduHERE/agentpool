from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_session_id() -> str:
    return f"ap_{uuid.uuid4().hex[:12]}"


def repo_hash(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")


def append_jsonl(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, sort_keys=True, default=str) + "\n")


def subprocess_env(
    env: dict[str, str] | None = None,
    *,
    terminal_dumb: bool = False,
) -> dict[str, str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    if terminal_dumb:
        merged.update(
            {
                "TERM": "dumb",
                "NO_COLOR": "1",
                "CLICOLOR": "0",
                "FORCE_COLOR": "0",
            }
        )
    return merged


def run_capture(
    args: list[str],
    cwd: Path | None = None,
    timeout: float = 10,
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    terminal_dumb: bool = False,
) -> subprocess.CompletedProcess[str]:
    stdin = subprocess.PIPE if input_text is not None else subprocess.DEVNULL
    proc = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=subprocess_env(env, terminal_dumb=terminal_dumb),
    )
    try:
        stdout, stderr = proc.communicate(input_text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        terminate_process_group(proc)
        stdout = exc.stdout or ""
        stderr = exc.stderr or "timed out"
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(args, 124, stdout, stderr)
    return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)


def popen_text(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    terminal_dumb: bool = False,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=subprocess_env(env, terminal_dumb=terminal_dumb),
    )


def terminate_process_group(proc: subprocess.Popen[str], timeout: float = 1) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except OSError:
        try:
            proc.terminate()
        except OSError:
            return
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            try:
                proc.kill()
            except OSError:
                return


def expand_user_path(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser()
