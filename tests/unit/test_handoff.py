"""Unit tests for the per-session handoff document renderer."""

from __future__ import annotations


def _mk_entries(specs):
    from hippocampus.storage import ledger, sessions

    sid = sessions.open_session("pytest")
    for kind, content, *rest in specs:
        details = rest[0] if rest else None
        ledger.log_entry(sid, "pytest", kind, content, details=details)
    return sid, ledger.current_entries(sid)


def test_current_goal_latest_goal_wins(hippo_env):
    from hippocampus import handoff

    _, entries = _mk_entries(
        [
            ("goal", "Original goal"),
            ("done", "step 1"),
            ("goal", "Revised goal"),
        ]
    )
    assert handoff.current_goal(entries).content == "Revised goal"


def test_current_goal_falls_back_to_first_ask(hippo_env):
    from hippocampus import handoff

    _, entries = _mk_entries([("ask", "Please fix the login bug"), ("done", "found it")])
    assert handoff.current_goal(entries).content == "Please fix the login bug"


def test_render_is_unabridged_and_ordered(hippo_env):
    from hippocampus import handoff

    long_done = "x" * 500  # working block truncates at 220; handoff must not
    sid, entries = _mk_entries(
        [
            ("goal", "Ship the feature"),
            ("done", "first thing"),
            ("done", long_done, "extra details line"),
            ("blocker", "waiting on API key"),
            ("decision", "use sqlite"),
        ]
    )
    doc = handoff.render_handoff(session_id=sid, client="pytest", entries=entries)
    assert "## Main goal" in doc
    assert "Ship the feature" in doc
    assert long_done in doc
    assert "extra details line" in doc
    assert "waiting on API key" in doc
    assert "use sqlite" in doc
    assert doc.index("first thing") < doc.index(long_done)  # chronological


def test_render_goal_history_when_goal_changes(hippo_env):
    from hippocampus import handoff

    sid, entries = _mk_entries([("goal", "Goal A"), ("goal", "Goal B")])
    doc = handoff.render_handoff(session_id=sid, client="pytest", entries=entries)
    # Main goal section shows the latest; history lists both.
    assert "### Goal history" in doc
    assert doc.index("Goal B") < doc.index("### Goal history")


def test_write_handoff_idempotent(hippo_env):
    from hippocampus import handoff

    sid, entries = _mk_entries([("goal", "G")])
    path, changed = handoff.write_handoff(session_id=sid, client="pytest", entries=entries)
    assert changed is True
    assert path.exists()
    _, changed_again = handoff.write_handoff(session_id=sid, client="pytest", entries=entries)
    assert changed_again is False


def test_final_summary_and_status(hippo_env):
    from hippocampus import handoff

    sid, entries = _mk_entries([("goal", "G"), ("done", "D")])
    doc = handoff.render_handoff(
        session_id=sid,
        client="pytest",
        entries=entries,
        status="completed",
        final_summary="All shipped.",
    )
    assert "- **status**: completed" in doc
    assert "## Final summary" in doc
    assert "All shipped." in doc
