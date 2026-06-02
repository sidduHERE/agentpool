from __future__ import annotations

from agentpool.redaction import redact_text


def test_redacts_common_token_shapes() -> None:
    github_token = "ghp_" + ("a" * 26)
    aws_key = "AKIA" + ("1" * 16)
    google_key = "AI" + "za" + ("0" * 30)
    slack_token = "xox" + "b-" + ("1" * 12) + "-" + ("a" * 26)
    jwt = "ey" + "J" + ("a" * 12) + "." + ("b" * 12) + "." + ("c" * 12)
    private_key = "-----BEGIN " + "PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
    text = "\n".join(
        [
            "Authorization: Bearer abc.def.ghi",
            "Authorization: token github-token-value",
            "Authorization: Basic base64-value",
            "github=" + github_token,
            "aws=" + aws_key,
            "google=" + google_key,
            "slack=" + slack_token,
            "db=postgres://user:password@example.com/db",
            "jwt=" + jwt,
            private_key,
        ]
    )

    redacted = redact_text(text)

    assert "abc.def.ghi" not in redacted
    assert "github-token-value" not in redacted
    assert "base64-value" not in redacted
    assert "ghp_" not in redacted
    assert aws_key not in redacted
    assert "AIza" not in redacted
    assert "xoxb-" not in redacted
    assert "password@example.com" not in redacted
    assert jwt not in redacted
    assert "BEGIN PRIVATE KEY" not in redacted
    assert "[REDACTED]" in redacted


def test_redacts_diff_text_before_artifact_write(tmp_path) -> None:
    from agentpool.artifacts import collect_artifacts, initialize_artifacts
    from agentpool.models import AgentSession, RuntimeKind, SessionState

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# hi\n", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
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
        cwd=repo,
        check=True,
        capture_output=True,
    )
    secret = "ghp_" + ("z" * 26)
    (repo / "README.md").write_text(f"# hi\nsecret={secret}\n", encoding="utf-8")

    artifact_dir = tmp_path / "artifacts"
    session = AgentSession(
        id="ap_test",
        provider_id="fake-question",
        model=None,
        harness="fake",
        account=None,
        repo_path=str(repo),
        task="test",
        role="reviewer",
        runtime=RuntimeKind.TMUX,
        state=SessionState.COMPLETED,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        artifact_dir=str(artifact_dir),
        transcript_path=str(artifact_dir / "transcript.txt"),
        events_path=str(artifact_dir / "events.jsonl"),
    )
    initialize_artifacts(session, "prompt")

    collect_artifacts(session, screen="", include_diff=True)

    diff = (artifact_dir / "diff.patch").read_text(encoding="utf-8")
    assert secret not in diff
    assert "[REDACTED]" in diff
