"""Session settlement: recalled-and-done becomes weak helped, the rest neutral."""

from __future__ import annotations

import pytest


@pytest.fixture
def ev(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPO_CONFIDENCE_MODEL", "evidence")
    monkeypatch.setenv("HIPPO_LOG_PROGRESS_RECALL_BOOST_K", "0")
    from hippocampus.dynamics import boost as boost_dyn
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F, sessions

    return boost_dyn, T, F, sessions


def _scenario(ev, with_done=True):
    boost_dyn, T, F, sessions = ev
    sid = sessions.current_session_id("pytest")
    recalled, injected, auto = (F.create(x).id for x in ("recalled", "injected", "auto"))
    sessions.log_access(sid, injected, via="inject")
    boost_dyn.boost(auto, session_id=sid, via="auto")
    boost_dyn.boost(recalled, session_id=sid)
    if with_done:
        T.log_progress(kind="done", content="finished the task")
    return sid, recalled, injected, auto


def _conf(F, *ids):
    return [F.get(i).confidence for i in ids]


def test_end_progress_settles(ev):
    _, T, F, _ = ev
    sid, recalled, injected, auto = _scenario(ev)
    T.end_progress()
    assert _conf(F, recalled, injected, auto) == [pytest.approx(0.51), 0.5, 0.5]


def test_no_done_means_neutral(ev):
    _, T, F, _ = ev
    sid, recalled, *_ = _scenario(ev, with_done=False)
    T.end_progress()
    assert F.get(recalled).confidence == 0.5


def test_hurt_in_session_blocks_weak_helped(ev):
    _, T, F, _ = ev
    from hippocampus.dynamics import outcomes

    sid, recalled, *_ = _scenario(ev)
    outcomes.apply(sid, recalled, "hurt", "medium", "negation")
    T.end_progress()
    assert F.get(recalled).confidence == pytest.approx(0.42)


def test_settle_is_idempotent(ev):
    from hippocampus.dynamics import outcomes

    sid, recalled, *_ = _scenario(ev)
    assert outcomes.settle_session(sid) == 3
    assert outcomes.settle_session(sid) == 0
    assert ev[2].get(recalled).confidence == pytest.approx(0.51)


def test_idle_auto_end_settles(ev, monkeypatch):
    _, T, F, sessions = ev
    monkeypatch.setenv("HIPPO_AUTO_END_IDLE_MINUTES", "1")
    sid, recalled, *_ = _scenario(ev)
    from hippocampus.storage.db import get_conn

    with get_conn() as conn:
        conn.execute("UPDATE session_ledger SET created_at = '2020-01-01T00:00:00.000000Z'")
        conn.execute("UPDATE session_accesses SET accessed_at = '2019-12-31T00:00:00.000000Z'")
        conn.execute("UPDATE sessions SET started_at = '2019-12-30T00:00:00.000000Z'")
    assert T.auto_end_idle_sessions()["ended"] == 1
    assert F.get(recalled).confidence == pytest.approx(0.51)


def test_decay_sweep_settles_pending_and_is_idempotent(ev):
    _, _, F, sessions = ev
    from hippocampus.dynamics import decay, outcomes

    sid, recalled, *_ = _scenario(ev)
    sessions.close_session(sid)
    decay.run_decay_cycle()
    decay.run_decay_cycle()
    assert F.get(recalled).helped_count == 0
    assert F.get(recalled).confidence == pytest.approx(0.51)
    assert outcomes.settle_pending() == 0


def test_access_mode_does_not_settle(hippo_env):
    from hippocampus.dynamics import outcomes
    from hippocampus.storage import sessions

    sid = sessions.current_session_id("pytest")
    sessions.close_session(sid)
    assert outcomes.settle_session(sid) == 0 and outcomes.settle_pending() == 0
