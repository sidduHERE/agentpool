from __future__ import annotations

import json
import shutil
from pathlib import Path

from agentpool.config import TerminalControlConfig
from agentpool.models import RuntimeKind, TerminalControlSessionRef, ToolError
from agentpool.utils import run_capture


KEY_MAP = {
    "enter": "enter",
    "return": "enter",
    "c-m": "enter",
    "escape": "escape",
    "esc": "escape",
    "tab": "tab",
    "btab": "shift-tab",
    "shift-tab": "shift-tab",
    "backspace": "backspace",
    "delete": "delete",
    "dc": "delete",
    "home": "home",
    "end": "end",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "pageup": "page-up",
    "page-up": "page-up",
    "pagedown": "page-down",
    "page-down": "page-down",
    "c-c": "ctrl-c",
}


class TerminalControlRuntime:
    kind = RuntimeKind.TERMINAL_CONTROL

    def __init__(self, config: TerminalControlConfig | None = None):
        self.config = config or TerminalControlConfig()
        configured = self.config.binary
        self.binary = configured if Path(configured).expanduser().exists() else shutil.which(configured)

    def require_binary(self) -> str:
        if not self.binary:
            raise ToolError(
                "TERMINAL_CONTROL_NOT_FOUND",
                "Terminal Control runtime was selected, but termctrl is not on PATH.",
                {"binary": self.config.binary},
            )
        return str(Path(self.binary).expanduser()) if "/" in self.binary else self.binary

    def spawn(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str] | None,
        session_name: str,
    ) -> TerminalControlSessionRef:
        termctrl = self.require_binary()
        cwd.mkdir(parents=True, exist_ok=True)
        args = [
            termctrl,
            "start",
            "--cols",
            str(self.config.cols),
            "--rows",
            str(self.config.rows),
            "--cwd",
            str(cwd),
        ]
        if self.config.host:
            args.extend(["--host", self.config.host])
        record_path = _recording_path(env, session_name) if self.config.artifacts.record else None
        if record_path:
            record_path.parent.mkdir(parents=True, exist_ok=True)
            args.extend(["--record", str(record_path)])
        args.extend([session_name, "--", *command])
        proc = run_capture(args, env=env)
        if proc.returncode != 0:
            raise ToolError(
                "SPAWN_FAILED",
                f"Failed to create Terminal Control session {session_name}.",
                {"stderr": proc.stderr, "stdout": proc.stdout, "command": command},
            )
        return TerminalControlSessionRef(session_name=session_name)

    def capture(
        self,
        ref: TerminalControlSessionRef,
        lines: int = 300,
        timeout_seconds: float | None = None,
    ) -> str:
        termctrl = self.require_binary()
        default_timeout = max(1, (self.config.deadline_ms / 1000) + 2)
        timeout = max(0.1, timeout_seconds) if timeout_seconds is not None else default_timeout
        deadline_ms = max(100, int(timeout * 1000) - 250)
        proc = run_capture(
            [
                termctrl,
                "show",
                "--format",
                "txt",
                "--settle-ms",
                str(self.config.settle_ms),
                "--deadline-ms",
                str(min(self.config.deadline_ms, deadline_ms)),
                ref.session_name,
            ],
            timeout=timeout,
        )
        if proc.returncode == 124:
            raise ToolError(
                "RUNTIME_CAPTURE_TIMEOUT",
                f"Timed out capturing Terminal Control session {ref.session_name}.",
                {"stderr": proc.stderr, "timeout_seconds": timeout_seconds},
            )
        if proc.returncode != 0:
            raise ToolError(
                "RUNTIME_SESSION_NOT_FOUND",
                f"Could not capture Terminal Control session {ref.session_name}.",
                {"stderr": proc.stderr},
            )
        if lines <= 0:
            return proc.stdout
        return "\n".join(proc.stdout.splitlines()[-lines:])

    def send_message(self, ref: TerminalControlSessionRef, text: str, submit: bool = True) -> None:
        if text == "" and submit:
            self.send_keys(ref, ["Enter"])
            return
        termctrl = self.require_binary()
        proc = run_capture([termctrl, "send", ref.session_name, "--stdin"], input_text=text)
        if proc.returncode != 0:
            raise ToolError(
                "TERMINAL_CONTROL_SEND_FAILED",
                f"Could not send text to Terminal Control session {ref.session_name}.",
                {"stderr": proc.stderr},
            )
        if submit and not text.endswith("\n"):
            self.send_keys(ref, ["Enter"])

    def send_keys(self, ref: TerminalControlSessionRef, keys: list[str]) -> None:
        termctrl = self.require_binary()
        mapped = [_map_key(key) for key in keys]
        proc = run_capture([termctrl, "send", ref.session_name, *mapped])
        if proc.returncode != 0:
            raise ToolError(
                "RUNTIME_SESSION_NOT_FOUND",
                f"Could not send keys to Terminal Control session {ref.session_name}.",
                {"stderr": proc.stderr, "keys": keys},
            )

    def interrupt(self, ref: TerminalControlSessionRef) -> None:
        self.send_keys(ref, ["C-c"])

    def attach_command(self, ref: TerminalControlSessionRef) -> str:
        return f"termctrl show {ref.session_name}"

    def live_control(self, ref: TerminalControlSessionRef, allow_raw_keys: bool) -> dict[str, object]:
        name = ref.session_name
        return {
            "can_capture_screen": True,
            "can_send_message": True,
            "can_send_keys": allow_raw_keys,
            "can_interrupt": True,
            "can_attach": False,
            "attach_kind": "snapshot",
            "commands": {
                "show": f"termctrl show {name}",
                "status": f"termctrl status {name} --json",
                "logs": f"termctrl logs {name}",
                "stop": f"termctrl stop {name}",
            },
        }

    def terminate(self, ref: TerminalControlSessionRef) -> None:
        termctrl = self.require_binary()
        proc = run_capture([termctrl, "stop", ref.session_name])
        if proc.returncode != 0 and self.exists(ref):
            raise ToolError(
                "TERMINAL_CONTROL_STOP_FAILED",
                f"Could not stop Terminal Control session {ref.session_name}.",
                {"stderr": proc.stderr},
            )

    def exists(self, ref: TerminalControlSessionRef) -> bool:
        if not self.binary:
            return False
        proc = run_capture([self.require_binary(), "status", "--json", ref.session_name])
        if proc.returncode != 0:
            return False
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return True
        state = str(payload.get("state") or "").lower()
        return state in {"", "running", "starting"}

    def extra_artifacts(
        self,
        ref: TerminalControlSessionRef,
        artifact_dir: Path,
        failed: bool = False,
    ) -> list[dict[str, str]]:
        formats: list[str] = []
        if self.config.artifacts.save_text:
            formats.append("txt")
        if self.config.artifacts.save_json:
            formats.append("json")
        if failed and self.config.artifacts.save_svg_on_failure:
            formats.append("svg")
        if failed and self.config.artifacts.save_png_on_failure:
            formats.append("png")
        if not formats:
            return []
        termctrl = self.require_binary()
        out_dir = artifact_dir / "raw" / "terminal-control"
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = out_dir / "current"
        args = [termctrl, "save", ref.session_name, "--out", str(stem)]
        for fmt in formats:
            args.extend(["--format", fmt])
        proc = run_capture(args, timeout=max(2, (self.config.deadline_ms / 1000) + 3))
        if proc.returncode != 0:
            error_path = out_dir / "save-error.txt"
            error_path.write_text(proc.stderr or proc.stdout, encoding="utf-8")
            return [{"kind": "terminal_control_save_error", "path": str(error_path)}]
        artifacts = []
        for fmt in formats:
            path = stem.with_suffix(f".{fmt}") if len(formats) > 1 else stem
            if len(formats) == 1:
                path = stem
            if not path.exists():
                candidate = stem.with_suffix(f".{fmt}")
                path = candidate if candidate.exists() else path
            if path.exists():
                artifacts.append({"kind": f"terminal_control_{fmt}", "path": str(path)})
        recording = out_dir / f"{ref.session_name}.termctrl"
        if recording.exists():
            artifacts.append({"kind": "terminal_control_recording", "path": str(recording)})
        return artifacts


def _map_key(key: str) -> str:
    normalized = key.strip().lower().replace("_", "-")
    if normalized in KEY_MAP:
        return KEY_MAP[normalized]
    if len(normalized) == 3 and normalized.startswith("c-") and normalized[2].isalpha():
        return f"ctrl-{normalized[2]}"
    if normalized.startswith("ctrl-") and len(normalized) == 6 and normalized[-1].isalpha():
        return normalized
    raise ToolError(
        "TERMINAL_CONTROL_UNSUPPORTED_KEY",
        f"Terminal Control does not support key {key!r}.",
        {"key": key},
    )


def _recording_path(env: dict[str, str] | None, session_name: str) -> Path | None:
    artifact_dir = (env or {}).get("AGENTPOOL_ARTIFACT_DIR")
    if not artifact_dir:
        return None
    return Path(artifact_dir) / "raw" / "terminal-control" / f"{session_name}.termctrl"
