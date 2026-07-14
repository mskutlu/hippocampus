from __future__ import annotations

import json
import stat


def test_transcript_capture_is_opt_in(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPO_TRANSCRIPT_CAPTURE_ENABLED", "false")

    result = tools.log_transcript(role="user", content="private prompt")

    assert result == {"logged": False, "reason": "transcript_capture_disabled"}


def test_runtime_paths_are_private(hippo_env):
    from hippocampus import config
    from hippocampus import handoff
    from hippocampus.storage import sessions

    session_id = sessions.open_session("pytest", session_key="private")
    pointer = config.SESSION_POINTER_DIR / "pytest" / "private.id"
    handoff_path, _ = handoff.write_handoff(
        session_id=session_id,
        client="pytest",
        entries=[],
    )

    assert session_id
    assert stat.S_IMODE(config.HIPPOCAMPUS_HOME.stat().st_mode) == 0o700
    assert stat.S_IMODE(config.DB_PATH.stat().st_mode) == 0o600
    assert stat.S_IMODE(pointer.stat().st_mode) == 0o600
    assert stat.S_IMODE(handoff_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(handoff_path.stat().st_mode) == 0o600


def test_transcript_retention_export_and_purge(hippo_env, monkeypatch):
    from click.testing import CliRunner

    from hippocampus.cli.main import cli
    from hippocampus.mcp import tools
    from hippocampus.storage import transcript
    from hippocampus.storage.db import get_conn

    monkeypatch.setenv("HIPPO_TRANSCRIPT_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("HIPPO_TRANSCRIPT_RETENTION_DAYS", "30")
    tools.log_transcript(role="user", content="expired")
    with get_conn() as conn:
        conn.execute(
            "UPDATE session_transcript SET created_at = '2020-01-01T00:00:00.000Z'"
        )
    tools.log_transcript(role="assistant", content="current")
    assert [entry.content for entry in transcript.all_entries()] == ["current"]

    export_path = hippo_env["home"] / "transcript.jsonl"
    runner = CliRunner()
    exported = runner.invoke(cli, ["transcript", "export", str(export_path)])
    assert exported.exit_code == 0, exported.output
    assert json.loads(export_path.read_text(encoding="utf-8"))["content"] == "current"
    assert stat.S_IMODE(export_path.stat().st_mode) == 0o600

    purged = runner.invoke(cli, ["transcript", "purge", "--all"])
    assert purged.exit_code == 0, purged.output
    assert transcript.all_entries() == []


def test_session_cleanup_preserves_transcript_only_sessions(hippo_env, monkeypatch):
    from hippocampus import maintenance
    from hippocampus.mcp import tools
    from hippocampus.storage import sessions, transcript

    monkeypatch.setenv("HIPPO_TRANSCRIPT_CAPTURE_ENABLED", "true")
    logged = tools.log_transcript(role="user", content="retain me")
    session_id = logged["entry"]["session_id"]
    sessions.close_session(session_id)

    result = maintenance.cleanup_sessions(dry_run=False)

    assert result["deleted_empty_ended"] == 0
    assert [entry.content for entry in transcript.current_entries(session_id)] == ["retain me"]
