from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentpool.config import AgentPoolConfig, StorageConfig
from agentpool.models import ObserveEvent, SessionState, SpawnWorkerRequest
from agentpool.session_manager import SessionManager
from agentpool.store import Store


pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is required")


@pytest.fixture()
def manager(tmp_path: Path) -> SessionManager:
    config = AgentPoolConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "agentpool.sqlite"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    return SessionManager(config=config, store=Store(tmp_path / "agentpool.sqlite"))


def test_fake_question_tmux_flow(manager: SessionManager, tmp_path: Path) -> None:
    result = manager.spawn_worker(
        SpawnWorkerRequest(
            provider_id="fake-question",
            task="Ask one question, then finish.",
            repo_path=str(tmp_path),
            isolation="read_only",
        )
    )
    session_id = result["session"]["id"]
    try:
        observed = manager.observe_worker(
            session_id,
            wait_for=["question"],
            timeout_seconds=8,
            max_lines=200,
        )
        assert observed.event == ObserveEvent.QUESTION
        assert observed.state == SessionState.AWAITING_USER_INPUT

        send = manager.send_worker_message(session_id, "Inspect migrations first.")
        assert send["ok"] is True

        done = manager.observe_worker(
            session_id,
            wait_for=["completed"],
            timeout_seconds=8,
            max_lines=300,
        )
        assert done.event == ObserveEvent.COMPLETED

        collected = manager.collect_worker_artifacts(session_id, mark_completed=True)
        assert Path(collected["artifact_dir"], "metadata.json").exists()
        assert Path(collected["artifact_dir"], "transcript.txt").exists()
    finally:
        manager.terminate_worker(session_id)


def test_fake_control_plane_full_lifecycle(manager: SessionManager, tmp_path: Path) -> None:
    result = manager.spawn_worker(
        SpawnWorkerRequest(
            provider_id="fake-question",
            task="Exercise every v0.1 control-plane verb.",
            repo_path=str(tmp_path),
            isolation="read_only",
        )
    )
    session_id = result["session"]["id"]
    try:
        attach = manager.attach_info(session_id)
        assert attach["attach_command"].startswith("tmux attach -t ")
        assert attach["pane_target"].endswith(":0.0")

        question = manager.observe_worker(session_id, wait_for=["question"], timeout_seconds=8)
        assert question.event == ObserveEvent.QUESTION

        enter = manager.send_worker_message(session_id, "", submit=True)
        assert enter["ok"] is True

        sent = manager.send_worker_message(session_id, "Inspect the cache layer first.")
        assert sent["ok"] is True

        completed = manager.observe_worker(session_id, wait_for=["completed"], timeout_seconds=8)
        assert completed.event == ObserveEvent.COMPLETED

        interrupted = manager.interrupt_worker(session_id)
        assert interrupted["ok"] is True

        collected = manager.collect_worker_artifacts(session_id, mark_completed=True)
        artifact_dir = Path(collected["artifact_dir"])
        assert collected["state"] == SessionState.COMPLETED.value
        assert (artifact_dir / "latest_screen.txt").exists()
        assert (artifact_dir / "summary.md").read_text(encoding="utf-8").strip()
        assert any(artifact["kind"] == "events" for artifact in collected["artifacts"])

        terminated = manager.terminate_worker(session_id, reason="test cleanup")
        assert terminated["ok"] is True
        assert terminated["state"] == SessionState.COMPLETED.value

        stored = manager.get_session(session_id)["session"]
        assert stored["state"] == SessionState.COMPLETED.value
        event_types = [event["event_type"] for event in manager.store.list_events(session_id)]
        assert event_types[:2] == ["spawn", "send_initial_prompt"]
        assert "send_message" in event_types
        assert "interrupt" in event_types
        assert "collect" in event_types
        assert event_types[-1] == "terminate"
    finally:
        session = manager.store.get_session(session_id)
        if session and session.tmux and manager.runtime.exists(session.tmux):
            manager.terminate_worker(session_id, reason="test finalizer")


def test_worktree_patch_collects_diff(manager: SessionManager, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# test\n", encoding="utf-8")
    assert _run(["git", "init"], repo) == 0
    assert _run(["git", "add", "README.md"], repo) == 0
    assert (
        _run(
            [
                "git",
                "-c",
                "user.name=AgentPool",
                "-c",
                "user.email=agentpool@example.com",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-m",
                "init",
            ],
            repo,
        )
        == 0
    )

    result = manager.spawn_worker(
        SpawnWorkerRequest(
            provider_id="fake-patch",
            task="Create the fake patch file.",
            repo_path=str(repo),
            role="implementer",
            isolation="worktree",
        )
    )
    session_id = result["session"]["id"]
    try:
        done = manager.observe_worker(session_id, wait_for=["completed"], timeout_seconds=8)
        assert done.event == ObserveEvent.COMPLETED
        collected = manager.collect_worker_artifacts(session_id)
        assert collected["git"]["dirty"] is True
        assert "agentpool_fake_patch.txt" in collected["git"]["changed_files"]
        assert not (repo / "agentpool_fake_patch.txt").exists()
    finally:
        manager.terminate_worker(session_id)


def _run(args: list[str], cwd: Path) -> int:
    import subprocess

    return subprocess.run(args, cwd=str(cwd), text=True, capture_output=True, check=False).returncode
