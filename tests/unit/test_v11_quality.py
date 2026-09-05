"""V11 Phase 1 — quality: distill cap, audit, purge-noise, mark, injection guard, tag cleaning."""

from __future__ import annotations

import pytest


def _ledger(sid, client="pytest"):
    from hippocampus.storage import ledger as L

    L.log_entry(sid, client, "goal", "Ship the thing")
    for i in range(8):
        L.log_entry(sid, client, "done", f"step {i} finished")
    L.log_entry(sid, client, "ask", "<task-notification> noisy </task-notification>")
    L.log_entry(sid, client, "ask", "please do it")
    L.log_entry(sid, client, "decision", "use sqlite")
    L.log_entry(sid, client, "blocker", "waiting on token")
    return L.current_entries(sid)


def test_distill_keeps_last_dones_and_drops_asks_and_markup(hippo_env):
    from hippocampus.mcp import tools as T
    from hippocampus.storage import sessions

    sid = sessions.open_session("pytest")
    entries = _ledger(sid)
    text = T._render_ledger_as_fragment(entries, explicit_summary=None)
    assert "**Goal**" in text and "**Decision**" in text and "**Blocker**" in text
    assert "step 7 finished" in text and "step 2 finished" not in text
    assert "please do it" not in text
    assert "<task-notification>" not in text


def test_distill_caps_length(hippo_env, monkeypatch):
    from hippocampus.mcp import tools as T
    from hippocampus.storage import ledger as L, sessions

    monkeypatch.setenv("HIPPO_DISTILL_MAX_CHARS", "200")
    sid = sessions.open_session("pytest")
    for i in range(20):
        L.log_entry(sid, "pytest", "decision", "d" * 50 + str(i))
    text = T._render_ledger_as_fragment(L.current_entries(sid), explicit_summary=None)
    assert len(text) <= 200


def test_derive_summary_skips_markup_goal(hippo_env):
    from hippocampus.mcp import tools as T
    from hippocampus.storage import ledger as L, sessions

    sid = sessions.open_session("pytest")
    L.log_entry(sid, "pytest", "goal", "<task-notification>x</task-notification>")
    L.log_entry(sid, "pytest", "ask", "real ask")
    assert T._derive_summary(L.current_entries(sid)) == "Session summary: real ask"


def test_tags_drop_pipeline_prefixes(hippo_env):
    from hippocampus.storage import fragments as F

    frag = F.create("c", summary="s", tags=["log_progress_auto:ask", "client:devin", "trigger:never", "acme", "devin"])
    assert frag.tags == ["devin", "acme"]
    F.update_fields(frag.id, add_tags=["log_progress:done", "cluster:frag_x", "extra"])
    assert F.get(frag.id).tags == ["devin", "extra", "acme"]


def test_mark_tool(hippo_env):
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    fid = T.remember(content="kafka consumers must be idempotent")["fragment"]["id"]
    assert T.mark(fid, useful=True)["fragment"]["confidence"] == 1.0
    out = T.mark(fid, useful=False)
    assert out["fragment"]["confidence"] == pytest.approx(0.98)
    assert T.mark("frag_missing", useful=True)["found"] is False
    assert F.get(fid).accessed == 1


def test_hook_injection_does_not_boost_but_records(hippo_env, monkeypatch):
    from hippocampus.clients import hook_context
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F, sessions

    fid = T.remember(content="Kafka consumers must be idempotent.", summary="kafka")["fragment"]["id"]
    sid = sessions.current_session_id("pytest")
    payload = hook_context.render_context(client="pytest", query="kafka", extra_query_streams=[])
    assert fid in payload
    assert F.get(fid).confidence == 0.5
    assert fid in sessions.injected_fragment_ids(sid)

    # The AI's own recall in the same session is not counted as a new access.
    T.recall(query="kafka")
    assert F.get(fid).confidence == 0.5


def test_decay_logs_one_summary_row(hippo_env):
    from hippocampus.dynamics import decay
    from hippocampus.storage import fragments as F
    from hippocampus.storage.db import get_ro_conn

    for i in range(5):
        F.create(f"c{i}", summary="s")
    decay.run_decay_cycle()
    with get_ro_conn() as conn:
        rows = conn.execute("SELECT fragment_id, reason FROM feedback_log WHERE kind='decay'").fetchall()
    assert len(rows) == 1
    assert rows[0]["fragment_id"] == "decay-cycle" and rows[0]["reason"] == "decayed=5"


def test_cleanup_deletes_stale_access_only_sessions(hippo_env):
    from hippocampus import maintenance
    from hippocampus.storage import fragments as F, sessions
    from hippocampus.storage.db import get_conn

    frag = F.create("c", summary="s")
    sid = sessions.open_session("pytest")
    sessions.log_access(sid, frag.id, via="inject")
    sessions.close_session(sid)
    with get_conn() as conn:
        conn.execute("UPDATE sessions SET started_at = '2020-01-01T00:00:00.000Z' WHERE id = ?", (sid,))
    out = maintenance.cleanup_sessions(dry_run=False)
    assert out["deleted_stale_ended"] == 1
    assert sessions.injected_fragment_ids(sid) == set()


def test_feedback_prune(hippo_env):
    from hippocampus.storage import feedback
    from hippocampus.storage.db import get_conn

    feedback.log("frag_a", "boost", delta=0.01)
    with get_conn() as conn:
        conn.execute("UPDATE feedback_log SET created_at = '2020-01-01T00:00:00.000Z'")
    feedback.log("frag_b", "boost", delta=0.01)
    assert feedback.prune(90) == 1
    assert len(feedback.recent()) == 1


def test_audit_and_purge_noise(hippo_env, monkeypatch):
    from hippocampus import maintenance
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F, sessions

    monkeypatch.setenv("HIPPO_DISTILL_MAX_CHARS", "300")
    noise = F.create("<task-notification> <task-id>x</task-id> never mind", summary="Rule (never): <task-notification>")
    keep = F.create("Kafka consumers must be idempotent.", summary="kafka")
    F.update_fields(keep.id, confidence=1.0)

    sid = sessions.open_session("pytest")
    entries = _ledger(sid)
    big = F.create("x" * 2000, summary="big summary", source_type="session-summary", source_ref=sid)
    orphan = F.create("y" * 2000, summary="orphan", source_type="session-summary", source_ref="sess_gone")

    report = maintenance.audit()
    assert report["ok"] is False
    assert report["metrics"]["noise_fragments"] == 1
    assert report["metrics"]["oversized_fragments"] == 2
    assert any(b.startswith("noise_fragments") for b in report["breaches"])

    preview = maintenance.purge_noise(dry_run=True)
    assert preview["deleted_noise"] == 1 and preview["backup"] is None
    assert F.get(noise.id) is not None

    out = maintenance.purge_noise(dry_run=False)
    assert out["deleted_noise"] == 1 and out["rerendered"] == 1 and out["truncated"] == 1
    assert out["backup"] and out["backup"].endswith(".db")
    assert F.get(noise.id) is None
    assert "**Goal**" in F.get(big.id).content
    assert len(F.get(orphan.id).content) <= 300

    after = maintenance.audit()
    assert after["metrics"]["noise_fragments"] == 0
    assert after["metrics"]["oversized_fragments"] == 0


def test_daily_maintenance_runs_all_steps(hippo_env):
    from hippocampus import maintenance

    out = maintenance.run_daily_maintenance(dry_run=False)
    assert {"archive", "sessions", "feedback_pruned", "reindex"} <= set(out)


def test_tombstone_written_on_delete(hippo_env):
    from hippocampus.storage import fragments as F
    from hippocampus.storage.db import get_ro_conn

    frag = F.create("c", summary="s")
    F.delete(frag.id)
    with get_ro_conn() as conn:
        row = conn.execute("SELECT fragment_id FROM fragment_tombstones").fetchone()
    assert row["fragment_id"] == frag.id
