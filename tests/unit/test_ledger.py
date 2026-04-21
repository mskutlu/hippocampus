"""Unit tests for storage.ledger (CRUD + dedup)."""

from __future__ import annotations

import pytest


def test_log_entry_auto_increments_turn(hippo_env):
    from hippocampus.storage import ledger, sessions

    sid = sessions.open_session("pytest")
    e1 = ledger.log_entry(sid, "pytest", "goal", "Build working memory")
    e2 = ledger.log_entry(sid, "pytest", "ask", "Add ledger table")
    e3 = ledger.log_entry(sid, "pytest", "done", "Wrote migration 002")
    assert e1.turn_index == 1
    assert e2.turn_index == 2
    assert e3.turn_index == 3


def test_log_entry_rejects_invalid_kind(hippo_env):
    from hippocampus.storage import ledger, sessions

    sid = sessions.open_session("pytest")
    with pytest.raises(ValueError):
        ledger.log_entry(sid, "pytest", "random", "nope")


def test_log_entry_rejects_empty_content(hippo_env):
    from hippocampus.storage import ledger, sessions

    sid = sessions.open_session("pytest")
    with pytest.raises(ValueError):
        ledger.log_entry(sid, "pytest", "ask", "   ")


def test_dedup_within_60s_window(hippo_env):
    from hippocampus.storage import ledger, sessions

    sid = sessions.open_session("pytest")
    a = ledger.log_entry(sid, "pytest", "done", "Built X")
    b = ledger.log_entry(sid, "pytest", "done", "Built X")
    assert a is not None
    assert b is None  # deduped


def test_dedup_does_not_cross_kind_or_content(hippo_env):
    from hippocampus.storage import ledger, sessions

    sid = sessions.open_session("pytest")
    a = ledger.log_entry(sid, "pytest", "done", "Built X")
    # Different content bypasses dedup
    b = ledger.log_entry(sid, "pytest", "done", "Built Y")
    # Different kind bypasses dedup
    c = ledger.log_entry(sid, "pytest", "ask", "Built X")
    assert a is not None and b is not None and c is not None


def test_current_entries_returns_all_ordered(hippo_env):
    from hippocampus.storage import ledger, sessions

    sid = sessions.open_session("pytest")
    ledger.log_entry(sid, "pytest", "ask", "first")
    ledger.log_entry(sid, "pytest", "done", "second")
    ledger.log_entry(sid, "pytest", "done", "third")
    entries = ledger.current_entries(sid)
    assert [e.content for e in entries] == ["first", "second", "third"]


def test_rotate_preserves_old_ledger(hippo_env):
    from hippocampus.storage import ledger, sessions

    sid1 = sessions.open_session("pytest")
    ledger.log_entry(sid1, "pytest", "ask", "old ask")
    ledger.log_entry(sid1, "pytest", "done", "old done")

    sid2 = sessions.rotate("pytest")
    assert sid2 != sid1

    # New session has no entries
    assert ledger.current_entries(sid2) == []
    # Old session's entries are still in the DB (preserved, not deleted)
    assert len(ledger.current_entries(sid1)) == 2


def test_resolve_flag(hippo_env):
    from hippocampus.storage import ledger, sessions

    sid = sessions.open_session("pytest")
    e = ledger.log_entry(sid, "pytest", "blocker", "waiting for reply")
    assert e.resolved is False
    assert ledger.resolve(e.id) is True
    entries = ledger.current_entries(sid)
    assert entries[0].resolved is True


def test_grouped_for_render_buckets(hippo_env):
    from hippocampus.storage import ledger, sessions

    sid = sessions.open_session("pytest")
    ledger.log_entry(sid, "pytest", "goal", "G")
    ledger.log_entry(sid, "pytest", "ask", "A1")
    ledger.log_entry(sid, "pytest", "ask", "A2")
    ledger.log_entry(sid, "pytest", "done", "D1")
    ledger.log_entry(sid, "pytest", "decision", "DC")
    ledger.log_entry(sid, "pytest", "blocker", "B")
    ledger.log_entry(sid, "pytest", "next", "N")

    entries = ledger.current_entries(sid)
    grouped = ledger.grouped_for_render(entries)
    assert grouped["goal"].content == "G"
    assert [e.content for e in grouped["asks"]] == ["A2", "A1"]  # newest first
    assert [e.content for e in grouped["dones"]] == ["D1"]
    assert [e.content for e in grouped["decisions"]] == ["DC"]
    assert [e.content for e in grouped["blockers"]] == ["B"]
    assert [e.content for e in grouped["nexts"]] == ["N"]
    assert grouped["turn_count"] == 7
