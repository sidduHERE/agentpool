from __future__ import annotations

import os
import signal
import shutil
import subprocess
from pathlib import Path

from agentpool.models import RuntimeKind, TmuxSessionRef, ToolError


class TmuxRuntime:
    kind = RuntimeKind.TMUX

    def __init__(self, tmux_binary: str | None = None):
        self.tmux_binary = tmux_binary or shutil.which("tmux")

    def require_tmux(self) -> str:
        if not self.tmux_binary:
            raise ToolError("TMUX_NOT_FOUND", "tmux is required for AgentPool v0.1.")
        return self.tmux_binary

    def spawn(
        self, command: list[str], cwd: Path, env: dict[str, str] | None, session_name: str
    ) -> TmuxSessionRef:
        tmux = self.require_tmux()
        cwd.mkdir(parents=True, exist_ok=True)
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        try:
            subprocess.run(
                [tmux, "new-session", "-d", "-s", session_name, "-c", str(cwd), *command],
                env=merged_env,
                text=True,
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise ToolError(
                "SPAWN_FAILED",
                f"Failed to create tmux session {session_name}.",
                {"stderr": exc.stderr, "stdout": exc.stdout, "command": command},
            ) from exc
        return TmuxSessionRef(session_name=session_name)

    def capture(self, ref: TmuxSessionRef, lines: int = 300) -> str:
        tmux = self.require_tmux()
        proc = subprocess.run(
            [tmux, "capture-pane", "-p", "-J", "-t", ref.target, "-S", f"-{lines}"],
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise ToolError(
                "TMUX_SESSION_NOT_FOUND",
                f"Could not capture tmux pane {ref.target}.",
                {"stderr": proc.stderr},
            )
        return proc.stdout

    def send_message(self, ref: TmuxSessionRef, text: str, submit: bool = True) -> None:
        if text == "" and submit:
            self.send_keys(ref, ["Enter"])
            return
        tmux = self.require_tmux()
        buffer_name = f"agentpool-{ref.session_name}"
        subprocess.run([tmux, "load-buffer", "-b", buffer_name, "-"], input=text, text=True, check=True)
        subprocess.run([tmux, "paste-buffer", "-b", buffer_name, "-t", ref.target], check=True)
        if submit and not text.endswith("\n"):
            self.send_keys(ref, ["Enter"])

    def send_keys(self, ref: TmuxSessionRef, keys: list[str]) -> None:
        tmux = self.require_tmux()
        proc = subprocess.run(
            [tmux, "send-keys", "-t", ref.target, *keys],
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise ToolError(
                "TMUX_SESSION_NOT_FOUND",
                f"Could not send keys to tmux pane {ref.target}.",
                {"stderr": proc.stderr, "keys": keys},
            )

    def interrupt(self, ref: TmuxSessionRef) -> None:
        self.send_keys(ref, ["C-c"])

    def attach_command(self, ref: TmuxSessionRef) -> str:
        return f"tmux attach -t {ref.session_name}"

    def terminate(self, ref: TmuxSessionRef) -> None:
        tmux = self.require_tmux()
        pgid = self._pane_process_group(ref)
        subprocess.run([tmux, "kill-session", "-t", ref.session_name], check=False)
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError:
                pass

    def exists(self, ref: TmuxSessionRef) -> bool:
        if not self.tmux_binary:
            return False
        proc = subprocess.run(
            [self.tmux_binary, "has-session", "-t", ref.session_name],
            text=True,
            capture_output=True,
            check=False,
        )
        return proc.returncode == 0

    def _pane_process_group(self, ref: TmuxSessionRef) -> int | None:
        if not self.tmux_binary:
            return None
        proc = subprocess.run(
            [self.tmux_binary, "display-message", "-p", "-t", ref.target, "#{pane_pid}"],
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            return None
        try:
            pane_pid = int(proc.stdout.strip())
            return os.getpgid(pane_pid)
        except (ValueError, ProcessLookupError, PermissionError):
            return None
