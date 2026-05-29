from __future__ import annotations

import time

from fake_common import read_initial_prompt


def main() -> None:
    print("Fake Idle Agent ready.")
    read_initial_prompt()
    print("Working...")
    time.sleep(30)


if __name__ == "__main__":
    main()
