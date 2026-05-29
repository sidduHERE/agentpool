from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentpool.models import RuntimeKind, TmuxSessionRef


class RuntimeAdapter(Protocol):
    kind: RuntimeKind

    def spawn(
        self, command: list[str], cwd: Path, env: dict[str, str], session_name: str
    ) -> TmuxSessionRef:
        ...

    def capture(self, ref: TmuxSessionRef, lines: int) -> str:
        ...

    def send_message(self, ref: TmuxSessionRef, text: str, submit: bool = True) -> None:
        ...

    def send_keys(self, ref: TmuxSessionRef, keys: list[str]) -> None:
        ...

    def interrupt(self, ref: TmuxSessionRef) -> None:
        ...

    def attach_command(self, ref: TmuxSessionRef) -> str:
        ...

    def terminate(self, ref: TmuxSessionRef) -> None:
        ...

    def exists(self, ref: TmuxSessionRef) -> bool:
        ...
