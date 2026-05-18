"""V9 W2 — auto-distill idle sessions to fragments."""

from __future__ import annotations


def _backdate_session(client: str) -> str:
    """Helper: backdate the current session of `client` so it counts as idle."""
    from hippocampus.storage import sessions as sessions_store
    from hippocampus.storage.db import get_conn

    sid = sessions_store.current_session_id(client, open_if_missing=False)
    with get_conn() as conn:
        conn.execute("UPDATE session_ledger SET created_at = '2020-01-01T00:00:00.000Z' WHERE session_id = ?", (sid,))
        conn.execute("UPDATE sessions SET started_at = '2020-01-01T00:00:00.000Z' WHERE id = ?", (sid,))
    return sid


def test_auto_distill_creates_session_summary_fragment(hippo_env, monkeypatch):
    from hippocampus import config
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    config.set_setting("auto_end_idle_minutes", 30)
    config.set_setting("auto_distill_min_entries", 2)

    tools.log_progress(kind="goal", content="Ship the audit fix")
    tools.log_progress(kind="done", content="Implemented decay-recency shield")
    tools.log_progress(kind="decision", content="Default auto-end is 60 min")
    old_sid = _backdate_session("devin")

    out = tools.auto_end_idle_sessions()
    assert out["ended"] == 1
    distilled = out["sessions"][0]["distilled_fragment_id"]
    assert distilled is not None

    # The fragment exists with the right tags and was sourced from the session
    fr = tools.get_fragment(distilled, boost_on_read=False)["fragment"]
    assert "session-summary" in fr["tags"]
    assert "auto-distilled" in fr["tags"]
    assert "devin" in fr["tags"]
    assert fr["source_type"] == "session-summary"
    assert fr["source_ref"] == old_sid


def test_auto_distill_skips_sessions_below_min_entries(hippo_env, monkeypatch):
    from hippocampus import config
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    config.set_setting("auto_end_idle_minutes", 30)
    config.set_setting("auto_distill_min_entries", 5)

    tools.log_progress(kind="ask", content="quick question")
    _backdate_session("devin")

    out = tools.auto_end_idle_sessions()
    assert out["ended"] == 1
    assert out["sessions"][0]["distilled_fragment_id"] is None


def test_auto_distill_off_when_min_entries_zero(hippo_env, monkeypatch):
    """If min_entries=0 the distill step is skipped (legacy behaviour)."""
    from hippocampus import config
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    config.set_setting("auto_end_idle_minutes", 30)
    config.set_setting("auto_distill_min_entries", 0)

    for kind in ("goal", "done", "decision"):
        tools.log_progress(kind=kind, content=f"{kind} thing")
    _backdate_session("devin")

    out = tools.auto_end_idle_sessions()
    assert out["ended"] == 1
    assert out["sessions"][0]["distilled_fragment_id"] is None
