from __future__ import annotations

import subprocess
from pathlib import Path

from agentpool import utils


def test_run_capture_detaches_from_host_terminal(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class FakeProcess:
        pid = 12345
        returncode = 0

        def communicate(self, input: str | None = None, timeout: float | None = None) -> tuple[str, str]:
            seen["communicate_input"] = input
            seen["communicate_timeout"] = timeout
            return "out", ""

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        seen["command"] = command
        seen.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(utils.subprocess, "Popen", fake_popen)

    result = utils.run_capture(["helper", "usage"], cwd=Path("/tmp"), timeout=3, terminal_dumb=True)

    assert result.stdout == "out"
    assert seen["command"] == ["helper", "usage"]
    assert seen["cwd"] == "/tmp"
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["stdout"] is subprocess.PIPE
    assert seen["stderr"] is subprocess.PIPE
    assert seen["text"] is True
    assert seen["start_new_session"] is True
    assert seen["communicate_timeout"] == 3
    env = seen["env"]
    assert isinstance(env, dict)
    assert env["TERM"] == "dumb"
    assert env["NO_COLOR"] == "1"
    assert env["CLICOLOR"] == "0"
    assert env["FORCE_COLOR"] == "0"


def test_run_capture_uses_pipe_only_for_explicit_input(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class FakeProcess:
        pid = 12345
        returncode = 0

        def communicate(self, input: str | None = None, timeout: float | None = None) -> tuple[str, str]:
            seen["communicate_input"] = input
            return "", ""

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        seen.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(utils.subprocess, "Popen", fake_popen)

    utils.run_capture(["tmux", "load-buffer"], input_text="hello")

    assert seen["stdin"] is subprocess.PIPE
    assert seen["communicate_input"] == "hello"
    assert seen["start_new_session"] is True


def test_run_capture_timeout_terminates_process_group(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class FakeProcess:
        pid = 12345
        returncode = None

        def communicate(self, input: str | None = None, timeout: float | None = None) -> tuple[str, str]:
            raise subprocess.TimeoutExpired(["slow"], timeout or 0, output="partial")

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            seen["terminated"] = True

        def wait(self, timeout: float | None = None) -> None:
            seen["wait_timeout"] = timeout
            self.returncode = -15

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr(utils.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(utils.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(utils.os, "killpg", lambda pgid, sig: seen.update({"pgid": pgid, "signal": sig}))

    result = utils.run_capture(["slow"], timeout=0.01)

    assert result.returncode == 124
    assert result.stdout == "partial"
    assert seen["pgid"] == 12345
    assert seen["wait_timeout"] == 1


def test_popen_text_detaches_from_host_terminal(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class FakeProcess:
        pass

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        seen["command"] = command
        seen.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(utils.subprocess, "Popen", fake_popen)

    proc = utils.popen_text(["codex", "app-server"], terminal_dumb=True)

    assert isinstance(proc, FakeProcess)
    assert seen["stdin"] is subprocess.PIPE
    assert seen["stdout"] is subprocess.PIPE
    assert seen["stderr"] is subprocess.PIPE
    assert seen["text"] is True
    assert seen["start_new_session"] is True
    env = seen["env"]
    assert isinstance(env, dict)
    assert env["TERM"] == "dumb"


def test_product_code_uses_subprocess_only_through_utils() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "agentpool"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.name == "utils.py":
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ("subprocess.run(", "subprocess.Popen(", "subprocess.check_output(", "subprocess.check_call("):
            if needle in text:
                offenders.append(f"{path.relative_to(root)} uses {needle}")

    assert offenders == []
