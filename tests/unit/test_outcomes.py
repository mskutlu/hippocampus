"""Evidence model: outcome deltas, ceiling, correction split, idempotency, counters."""

from __future__ import annotations

import pytest


@pytest.fixture
def ev(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPO_CONFIDENCE_MODEL", "evidence")
    from hippocampus.dynamics import outcomes
    from hippocampus.storage import fragments as F, sessions

    sid = sessions.current_session_id("pytest")

    def make(conf=0.5, **kw):
        frag = F.create("fact " + str(len(F.list_all())), **kw)
        return F.update_fields(frag.id, confidence=conf).id

    return outcomes, F, sid, make


@pytest.mark.parametrize(
    "outcome,strength,start,expected",
    [
        ("helped", "weak", 0.5, 0.5 + 0.02 * 0.5),
        ("helped", "medium", 0.5, 0.5 + 0.05 * 0.5),
        ("helped", "strong", 0.5, 0.5 + 0.15 * 0.5),
        ("hurt", "weak", 0.5, 0.46),
        ("hurt", "medium", 0.5, 0.42),
        ("hurt", "strong", 0.5, 0.30),
        ("hurt", "strong", 0.1, 0.0),
    ],
)
def test_deltas(ev, outcome, strength, start, expected):
    outcomes, F, sid, make = ev
    fid = make(start)
    outcomes.apply(sid, fid, outcome, strength, "t")
    assert F.get(fid).confidence == pytest.approx(expected)


def test_weak_helped_skipped_at_ceiling_but_recorded(ev):
    outcomes, F, sid, make = ev
    fid = make(0.8)
    assert outcomes.apply(sid, fid, "helped", "weak", "t") == 0.0
    frag = F.get(fid)
    assert frag.confidence == pytest.approx(0.8) and frag.evidence_at is None
    medium = outcomes.apply(sid, fid, "helped", "medium", "t2")
    assert medium > 0


def test_apply_is_idempotent_on_primary_key(ev):
    outcomes, F, sid, make = ev
    fid = make(0.5)
    assert outcomes.apply(sid, fid, "helped", "strong", "mark") is not None
    once = F.get(fid)
    assert outcomes.apply(sid, fid, "helped", "strong", "mark") is None
    again = F.get(fid)
    assert again.confidence == once.confidence and again.helped_count == 1


def test_counters_and_evidence_at(ev):
    outcomes, F, sid, make = ev
    fid = make(0.5)
    outcomes.apply(sid, fid, "helped", "weak", "a")
    assert F.get(fid).helped_count == 0
    outcomes.apply(sid, fid, "helped", "medium", "b")
    outcomes.apply(sid, fid, "hurt", "weak", "c")
    frag = F.get(fid)
    assert (frag.helped_count, frag.hurt_count) == (1, 1) and frag.evidence_at


def test_neutral_changes_nothing(ev):
    outcomes, F, sid, make = ev
    fid = make(0.5)
    outcomes.apply(sid, fid, "neutral", "weak", "settle")
    frag = F.get(fid)
    assert frag.confidence == 0.5 and frag.evidence_at is None


def test_auto_pin_needs_helped_count_and_no_hurt(ev):
    outcomes, F, sid, make = ev
    good, bad = make(0.5), make(0.5)
    outcomes.apply(sid, bad, "hurt", "weak", "x")
    for i in range(3):
        outcomes.apply(sid, good, "helped", "medium", f"s{i}")
        outcomes.apply(sid, bad, "helped", "medium", f"s{i}")
    assert F.get(good).pinned is True
    assert F.get(bad).pinned is False


def test_correction_split_over_many_targets(ev):
    outcomes, F, sid, make = ev
    from hippocampus.mcp import tools as T
    from hippocampus.storage import sessions

    ids = [make(0.5) for _ in range(6)]
    T.log_progress(kind="ask", content="fix the deploy")
    for fid in ids:
        sessions.log_access(sid, fid)
    T.log_progress(kind="done", content="deploy fixed")
    entry = T.log_progress(kind="ask", content="still broken")["entry"]
    assert outcomes.on_ask(sid, "still broken", entry["id"]) == 0  # already applied by log_progress
    for fid in ids:
        assert F.get(fid).confidence == pytest.approx(0.5 - 0.04 * 3 / 6)


def test_correction_fires_without_done_and_uses_previous_ask_window(ev):
    outcomes, F, sid, make = ev
    from hippocampus.mcp import tools as T
    from hippocampus.storage import sessions

    old, fresh = make(0.5), make(0.5)
    sessions.log_access(sid, old)
    T.log_progress(kind="ask", content="do a thing")
    sessions.log_access(sid, fresh)
    T.log_progress(kind="ask", content="still broken")
    assert F.get(old).confidence == 0.5
    assert F.get(fresh).confidence == pytest.approx(0.46)


def test_correction_without_prior_ask_falls_back_to_session_start(ev):
    outcomes, F, sid, make = ev
    from hippocampus.mcp import tools as T
    from hippocampus.storage import sessions

    fid = make(0.5)
    sessions.log_access(sid, fid)
    T.log_progress(kind="ask", content="it still doesnt work")
    assert F.get(fid).confidence == pytest.approx(0.46)
