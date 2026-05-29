from __future__ import annotations

import sys
import time


sys.stdout.reconfigure(line_buffering=True)


def read_initial_prompt() -> str:
    lines: list[str] = []
    saw_task = False
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        lines.append(line)
        if saw_task:
            break
        if line.strip() == "Task:":
            saw_task = True
    return "".join(lines)


def print_result(summary: str, files_changed: str = "None", blockers: str = "None") -> None:
    print("AGENTPOOL_RESULT_START")
    print("Summary:")
    print(summary)
    print("Findings:")
    print("Fake provider completed.")
    print("Files inspected:")
    print("None")
    print("Files changed:")
    print(files_changed)
    print("Commands run:")
    print("fake-agent")
    print("Tests run:")
    print("None")
    print("Blockers:")
    print(blockers)
    print("Confidence:")
    print("high")
    print("AGENTPOOL_RESULT_END")
    time.sleep(30)
