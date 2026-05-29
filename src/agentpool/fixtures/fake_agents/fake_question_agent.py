from __future__ import annotations

from fake_common import print_result, read_initial_prompt


def main() -> None:
    print("Fake Question Agent ready.")
    read_initial_prompt()
    print("I found two possible paths. Should I inspect migrations or auth middleware first?")
    answer = input()
    print(f"Steering received: {answer}")
    print_result("Question path completed after steering.")


if __name__ == "__main__":
    main()
