from __future__ import annotations

from fake_common import print_result, read_initial_prompt


def main() -> None:
    print("Fake Limit Agent ready.")
    read_initial_prompt()
    print("Approaching 5-hour limit - resets 5pm.")
    print_result("Limit warning emitted.")


if __name__ == "__main__":
    main()
