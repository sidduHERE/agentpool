from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from agentpool.config import AgentPoolConfig, StorageConfig
from agentpool.models import ObserveEvent, RuntimeKind, SpawnWorkerRequest
from agentpool.session_manager import SessionManager
from agentpool.store import Store


TERMCTRL_BINARY = os.environ.get("AGENTPOOL_TERMCTRL_BINARY") or shutil.which("termctrl")
pytestmark = pytest.mark.skipif(TERMCTRL_BINARY is None, reason="termctrl is required")


@pytest.fixture
def manager(tmp_path: Path) -> SessionManager:
    assert TERMCTRL_BINARY is not None
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    config.runtime.terminal_control.enabled = True
    config.runtime.terminal_control.binary = TERMCTRL_BINARY
    return SessionManager(config=config, store=Store(tmp_path / "agentpool.sqlite"))


def test_fake_question_terminal_control_flow(manager: SessionManager, tmp_path: Path) -> None:
    result = manager.spawn_worker(
        SpawnWorkerRequest(
            provider_id="fake-question",
            task="AgentPool Terminal Control smoke test. Ask one question, then finish after steering.",
            repo_path=str(tmp_path),
            runtime=RuntimeKind.TERMINAL_CONTROL,
            isolation="read_only",
        )
    )
    session_id = result["session"]["id"]
    try:
        assert result["session"]["runtime"] == RuntimeKind.TERMINAL_CONTROL.value
        assert result["live_control"]["can_attach"] is False
        assert result["attach_command"].startswith("termctrl show ")

        question = manager.observe_worker(session_id, wait_for=["question"], timeout_seconds=8)
        assert question.event == ObserveEvent.QUESTION
        assert manager.send_worker_message(session_id, "Inspect migrations.")["ok"] is True
        completed = manager.observe_worker(session_id, wait_for=["completed"], timeout_seconds=8)
        assert completed.event == ObserveEvent.COMPLETED

        collected = manager.collect_worker_artifacts(session_id, mark_completed=True)
        kinds = {artifact["kind"] for artifact in collected["artifacts"]}
        assert "terminal_control_json" in kinds
    finally:
        manager.terminate_worker(session_id, reason="test cleanup")
