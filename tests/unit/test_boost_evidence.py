"""Evidence model: recall records access but does not move confidence."""

from __future__ import annotations

import pytest


@pytest.fixture
def env(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPO_CONFIDENCE_MODEL", "evidence")
    from hippocampus.dynamics import boost as boost_dyn
    from hippocampus.storage import fragments as F, sessions

    return boost_dyn, F, sessions, sessions.current_session_id("pytest")


def _via(sid, fid):
    from hippocampus.storage.db import get_ro_conn

    with get_ro_conn() as conn:
        row = conn.execute(
            "SELECT via FROM session_accesses WHERE session_id = ? AND fragment_id = ?", (sid, fid)
        ).fetchone()
    return row["via"] if row else None


def test_boost_counts_access_but_keeps_confidence(env):
    boost_dyn, F, _, sid = env
    fid = F.create("alpha").id
    before = F.get(fid).confidence
    after = boost_dyn.boost(fid, session_id=sid)
    assert after.confidence == before and after.accessed == 1 and after.last_accessed_at
    assert _via(sid, fid) == "recall"


def test_boost_many_skips_cluster_propagation(env):
    boost_dyn, F, _, sid = env
    a, b, c = (F.create(x).id for x in ("a1", "b1", "c1"))
    boost_dyn.boost_many([a, b], session_id=sid)
    boost_dyn.boost_many([a, c], session_id=sid, cluster_propagate=True)
    from hippocampus.storage.db import get_ro_conn

    with get_ro_conn() as conn:
        rows = conn.execute("SELECT 1 FROM feedback_log WHERE reason LIKE 'cluster:%'").fetchall()
    assert rows == [] and F.get(b).confidence == 0.5


def test_inject_then_recall_upgrades_via(env):
    boost_dyn, F, sessions, sid = env
    fid = F.create("injected").id
    sessions.log_access(sid, fid, via="inject")
    assert _via(sid, fid) == "inject"
    boost_dyn.boost(fid, session_id=sid)
    assert _via(sid, fid) == "recall"
    sessions.log_access(sid, fid, via="inject")
    assert _via(sid, fid) == "recall"


def test_via_precedence_auto_over_inject(env):
    boost_dyn, F, sessions, sid = env
    fid = F.create("auto").id
    sessions.log_access(sid, fid, via="inject")
    boost_dyn.boost(fid, session_id=sid, via="auto")
    assert _via(sid, fid) == "auto"
    sessions.log_access(sid, fid, via="inject")
    assert _via(sid, fid) == "auto"


def test_mark_useful_and_forget_use_outcomes(env):
    boost_dyn, F, _, sid = env
    fid = F.create("m").id
    boost_dyn.mark_useful(fid, session_id=sid)
    frag = F.get(fid)
    assert frag.confidence == pytest.approx(0.5 + 0.15 * 0.5) and frag.helped_count == 1
    boost_dyn.apply_negative_feedback(fid, session_id=sid)
    assert F.get(fid).hurt_count == 1 and F.get(fid).confidence < frag.confidence


def test_auto_pin_by_counters_not_access(env):
    boost_dyn, F, _, sid = env
    fid = F.create("pinme").id
    for _ in range(60):
        boost_dyn.boost(fid, session_id=sid)
    assert F.get(fid).pinned is False
