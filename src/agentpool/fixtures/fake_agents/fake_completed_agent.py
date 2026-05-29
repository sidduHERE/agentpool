from __future__ import annotations

from fake_common import print_result, read_initial_prompt


def main() -> None:
    print("Fake Completed Agent ready.")
    read_initial_prompt()
    print_result("Completed immediately.")


if __name__ == "__main__":
    main()
