"""Unit tests for format_working_block rendering."""

from __future__ import annotations


def test_empty_session_render(hippo_env):
    from hippocampus.clients.injector import format_working_block
    from hippocampus import config

    block = format_working_block(
        session_id=None,
        client="devin",
        started_at=None,
        entries=None,
    )
    assert config.WORKING_MARKER_START in block
    assert config.WORKING_MARKER_END in block
    assert "Memory protocol" in block
    assert "no active session" in block


def test_populated_session_render(hippo_env):
    from hippocampus.clients.injector import format_working_block
    from hippocampus.storage import ledger, sessions
    from hippocampus import config

    sid = sessions.open_session("devin")
    ledger.log_entry(sid, "devin", "goal", "Ship working memory")
    ledger.log_entry(sid, "devin", "ask", "Build ledger table")
    ledger.log_entry(sid, "devin", "done", "Wrote migration 002")
    ledger.log_entry(sid, "devin", "decision", "Use dedup window 60s")
    ledger.log_entry(sid, "devin", "blocker", "waiting for tests")

    entries = ledger.current_entries(sid)
    block = format_working_block(
        session_id=sid,
        client="devin",
        started_at="2026-04-20T10:30:00Z",
        entries=entries,
    )

    assert "Ship working memory" in block
    assert "Build ledger table" in block
    assert "Wrote migration 002" in block
    assert "Use dedup window 60s" in block
    assert "waiting for tests" in block
    assert "turn: 5" in block
    # Block stays compact (spec budget is 3 KB)
    assert len(block) < 3000
