from __future__ import annotations

import os
import signal
import shutil
from pathlib import Path

from agentpool.models import RuntimeKind, TmuxSessionRef, ToolError
from agentpool.utils import run_capture


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
        proc = run_capture(
            [tmux, "new-session", "-d", "-s", session_name, "-c", str(cwd), *command],
            env=merged_env,
        )
        if proc.returncode != 0:
            raise ToolError(
                "SPAWN_FAILED",
                f"Failed to create tmux session {session_name}.",
                {"stderr": proc.stderr, "stdout": proc.stdout, "command": command},
            )
        return TmuxSessionRef(session_name=session_name)

    def capture(self, ref: TmuxSessionRef, lines: int = 300) -> str:
        tmux = self.require_tmux()
        proc = run_capture(
            [tmux, "capture-pane", "-p", "-J", "-t", ref.target, "-S", f"-{lines}"],
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
        load = run_capture([tmux, "load-buffer", "-b", buffer_name, "-"], input_text=text)
        if load.returncode != 0:
            raise ToolError(
                "TMUX_SEND_FAILED",
                f"Could not load tmux paste buffer for {ref.target}.",
                {"stderr": load.stderr},
            )
        paste = run_capture([tmux, "paste-buffer", "-b", buffer_name, "-t", ref.target])
        if paste.returncode != 0:
            raise ToolError(
                "TMUX_SEND_FAILED",
                f"Could not paste tmux buffer to {ref.target}.",
                {"stderr": paste.stderr},
            )
        if submit and not text.endswith("\n"):
            self.send_keys(ref, ["Enter"])

    def send_keys(self, ref: TmuxSessionRef, keys: list[str]) -> None:
        tmux = self.require_tmux()
        proc = run_capture(
            [tmux, "send-keys", "-t", ref.target, *keys],
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
        run_capture([tmux, "kill-session", "-t", ref.session_name])
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
        proc = run_capture(
            [self.tmux_binary, "has-session", "-t", ref.session_name],
        )
        return proc.returncode == 0

    def _pane_process_group(self, ref: TmuxSessionRef) -> int | None:
        if not self.tmux_binary:
            return None
        proc = run_capture(
            [self.tmux_binary, "display-message", "-p", "-t", ref.target, "#{pane_pid}"],
        )
        if proc.returncode != 0:
            return None
        try:
            pane_pid = int(proc.stdout.strip())
            return os.getpgid(pane_pid)
        except (ValueError, ProcessLookupError, PermissionError):
            return None
