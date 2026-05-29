from __future__ import annotations

from fake_common import print_result, read_initial_prompt


def main() -> None:
    print("Fake Approval Agent ready.")
    read_initial_prompt()
    print("Proceed? [y/N]")
    answer = input()
    print(f"Approval answer: {answer}")
    print_result("Approval flow completed.")


if __name__ == "__main__":
    main()
