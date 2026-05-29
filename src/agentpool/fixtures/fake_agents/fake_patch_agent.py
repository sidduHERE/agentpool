from __future__ import annotations

from pathlib import Path

from fake_common import print_result, read_initial_prompt


def main() -> None:
    print("Fake Patch Agent ready.")
    read_initial_prompt()
    target = Path("agentpool_fake_patch.txt")
    target.write_text("patched by fake agent\n", encoding="utf-8")
    print_result("Patch file written.", files_changed=str(target))


if __name__ == "__main__":
    main()
