from __future__ import annotations

import json
from pathlib import Path

from agentpool.config import TerminalControlConfig
from agentpool.runtimes.terminal_control import TerminalControlRuntime


def test_terminal_control_runtime_uses_named_session_cli(tmp_path: Path, monkeypatch) -> None:
    termctrl = tmp_path / "termctrl"
    log_path = tmp_path / "termctrl-log.jsonl"
    termctrl.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

argv = sys.argv[1:]
log_path = Path(os.environ["TERMCTRL_LOG"])
cmd = argv[0]
stdin = sys.stdin.read() if cmd == "send" and "--stdin" in argv else ""
with log_path.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": argv, "stdin": stdin}) + "\\n")

if cmd == "status":
    print(json.dumps({"state": "running"}))
elif cmd == "show":
    print("visible screen")
elif cmd == "save":
    out = Path(argv[argv.index("--out") + 1])
    formats = [argv[index + 1] for index, value in enumerate(argv) if value == "--format"]
    out.parent.mkdir(parents=True, exist_ok=True)
    for fmt in formats:
        out.with_suffix(f".{fmt}").write_text(fmt, encoding="utf-8")
sys.exit(0)
""",
        encoding="utf-8",
    )
    termctrl.chmod(0o755)
    monkeypatch.setenv("TERMCTRL_LOG", str(log_path))
    runtime = TerminalControlRuntime(TerminalControlConfig(binary=str(termctrl), cols=100, rows=40))

    ref = runtime.spawn(["/bin/echo", "hi"], tmp_path, {"AGENTPOOL_ARTIFACT_DIR": str(tmp_path)}, "ap_tc")
    runtime.send_message(ref, "hello", submit=True)
    runtime.send_keys(ref, ["C-m", "C-c", "PageDown"])
    captured = runtime.capture(ref, 20)
    exists = runtime.exists(ref)
    artifacts = runtime.extra_artifacts(ref, tmp_path / "artifacts", failed=True)
    runtime.terminate(ref)

    calls = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert calls[0]["argv"][:8] == [
        "start",
        "--cols",
        "100",
        "--rows",
        "40",
        "--cwd",
        str(tmp_path),
        "ap_tc",
    ]
    assert calls[1] == {"argv": ["send", "ap_tc", "--stdin"], "stdin": "hello"}
    assert calls[2]["argv"] == ["send", "ap_tc", "enter"]
    assert calls[3]["argv"] == ["send", "ap_tc", "enter", "ctrl-c", "page-down"]
    assert captured == "visible screen"
    assert exists is True
    assert {artifact["kind"] for artifact in artifacts} == {
        "terminal_control_json",
        "terminal_control_svg",
    }
