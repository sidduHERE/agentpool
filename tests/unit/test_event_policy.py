from __future__ import annotations

import pytest

from agentpool.config import AgentPoolConfig
from agentpool.artifacts import extract_result
from agentpool.event_detection import detect_event
from agentpool.models import ObserveEvent, SessionState, ToolError
from agentpool.policy import enforce_raw_keys_policy, enforce_spawn_policy


def test_detects_question_and_approval() -> None:
    question = detect_event("Should I inspect migrations first?")
    assert question.event == ObserveEvent.QUESTION
    assert question.state == SessionState.AWAITING_USER_INPUT

    approval = detect_event("Proceed? [y/N]")
    assert approval.event == ObserveEvent.APPROVAL_PROMPT
    assert approval.state == SessionState.AWAITING_APPROVAL

    trust = detect_event("Do you trust the contents of this directory?")
    assert trust.event == ObserveEvent.APPROVAL_PROMPT
    assert trust.state == SessionState.AWAITING_APPROVAL

    update = detect_event("✨ Update available! 0.129.0 -> 0.130.0\n2. Skip")
    assert update.event == ObserveEvent.APPROVAL_PROMPT
    assert update.state == SessionState.AWAITING_APPROVAL

    hooks = detect_event("Hooks need review\n2. Trust all and continue\n3. Continue without trusting")
    assert hooks.event == ObserveEvent.APPROVAL_PROMPT
    assert hooks.state == SessionState.AWAITING_APPROVAL


def test_detects_completion_marker() -> None:
    detection = detect_event("AGENTPOOL_RESULT_START\nSummary: ok\nAGENTPOOL_RESULT_END")
    assert detection.event == ObserveEvent.COMPLETED
    assert detection.state == SessionState.COMPLETED

    bullet = detect_event("• AGENTPOOL_RESULT_START\n  Summary: ok\n  AGENTPOOL_RESULT_END")
    assert bullet.event == ObserveEvent.COMPLETED
    assert bullet.state == SessionState.COMPLETED
    assert extract_result("• AGENTPOOL_RESULT_START\n  Summary: ok\n  AGENTPOOL_RESULT_END") == "Summary: ok"


def test_result_marker_names_in_prompt_do_not_mark_completed() -> None:
    detection = detect_event(
        "Task: answer with AGENTPOOL_RESULT_START, a Summary line containing ok, "
        "then AGENTPOOL_RESULT_END."
    )
    assert detection.event != ObserveEvent.COMPLETED

    template = (
        "AGENTPOOL_RESULT_START\n"
        "Summary:\n"
        "Findings:\n"
        "Files inspected:\n"
        "AGENTPOOL_RESULT_END\n"
    )
    assert detect_event(template).event != ObserveEvent.COMPLETED


def test_detects_smoke_done_but_not_prompt_line() -> None:
    prompt = "❯ Print the words AGENTPOOL, SMOKE, and DONE joined by underscores."
    assert detect_event(prompt).event != ObserveEvent.COMPLETED

    detection = detect_event("Claude response:\nAGENTPOOL_SMOKE_DONE\n")
    assert detection.event == ObserveEvent.COMPLETED
    assert detection.state == SessionState.COMPLETED


def test_mcp_startup_warning_does_not_mark_worker_failed() -> None:
    detection = detect_event("MCP client for `paper` failed to start: optional server unavailable")
    assert detection.event != ObserveEvent.ERROR
    assert detection.state != SessionState.FAILED

    auth_warning = detect_event("Auth error: OAuth token refresh failed while starting optional MCP server")
    assert auth_warning.event != ObserveEvent.ERROR
    assert auth_warning.state != SessionState.FAILED


def test_detects_strong_error_signals() -> None:
    detection = detect_event("Traceback (most recent call last):")
    assert detection.event == ObserveEvent.ERROR
    assert detection.state == SessionState.FAILED


def test_detects_provider_connectivity_errors() -> None:
    detection = detect_event("Unable to reach localhost. This usually means a firewall issue.")
    assert detection.event == ObserveEvent.ERROR
    assert detection.state == SessionState.FAILED

    cursor_login = detect_event("Cursor Agent\nPress any key to log in...")
    assert cursor_login.event == ObserveEvent.ERROR
    assert cursor_login.state == SessionState.FAILED


def test_stale_trust_prompt_in_scrollback_does_not_stick() -> None:
    screen = (
        "Do you trust the contents of this directory?\n"
        + "\n".join(f"startup line {index}" for index in range(90))
        + "\n›\n\n  tab to queue message 100% context left"
    )
    detection = detect_event(screen)
    assert detection.event != ObserveEvent.APPROVAL_PROMPT

    compact_screen = (
        "> You are in /tmp/repo\n\n"
        "  Do you trust the contents of this directory?\n"
        "› 1. Yes, continue\n"
        "  2. No, quit\n\n"
        "  Press enter to continue\n\n"
        "╭────╮\n"
        "│ >_ OpenAI Codex │\n"
        "╰────╯\n\n"
        "› Run /review on my current changes\n"
    )
    detection = detect_event(compact_screen)
    assert detection.event != ObserveEvent.APPROVAL_PROMPT


def test_policy_blocks_auto_and_raw_keys() -> None:
    config = AgentPoolConfig()
    with pytest.raises(ToolError):
        enforce_spawn_policy(config, "auto", "explorer", "read_only")
    with pytest.raises(ToolError):
        enforce_raw_keys_policy(config, ["C-c"])
