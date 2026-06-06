from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentpool.models import RuntimeKind, TerminalControlSessionRef, TmuxSessionRef


RuntimeRef = TmuxSessionRef | TerminalControlSessionRef


class RuntimeAdapter(Protocol):
    kind: RuntimeKind

    def spawn(
        self, command: list[str], cwd: Path, env: dict[str, str], session_name: str
    ) -> RuntimeRef:
        ...

    def capture(self, ref: RuntimeRef, lines: int) -> str:
        ...

    def send_message(self, ref: RuntimeRef, text: str, submit: bool = True) -> None:
        ...

    def send_keys(self, ref: RuntimeRef, keys: list[str]) -> None:
        ...

    def interrupt(self, ref: RuntimeRef) -> None:
        ...

    def attach_command(self, ref: RuntimeRef) -> str:
        ...

    def terminate(self, ref: RuntimeRef) -> None:
        ...

    def exists(self, ref: RuntimeRef) -> bool:
        ...

    def extra_artifacts(self, ref: RuntimeRef, artifact_dir: Path, failed: bool = False) -> list[dict[str, str]]:
        ...

    def live_control(self, ref: RuntimeRef, allow_raw_keys: bool) -> dict[str, object]:
        ...
