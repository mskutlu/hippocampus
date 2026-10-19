"""Shadow mode: outcomes are recorded, nothing else changes."""

from __future__ import annotations

import pytest


@pytest.fixture
def shadow(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPO_CONFIDENCE_MODEL", "shadow")
    monkeypatch.setenv("HIPPO_LOG_PROGRESS_RECALL_BOOST_K", "0")
    monkeypatch.setenv("HIPPO_INFERRED_NEGATION_ENABLED", "true")
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F, sessions

    return T, F, sessions, monkeypatch


def _rows():
    from hippocampus.storage.db import get_ro_conn

    with get_ro_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM fragment_outcomes ORDER BY source")]


def test_apply_records_prefixed_row_and_changes_nothing(shadow):
    from hippocampus.dynamics import outcomes

    _, F, sessions, _ = shadow
    fid = F.create("x").id
    sid = sessions.current_session_id("pytest")
    delta = outcomes.apply(sid, fid, "hurt", "strong", "explicit")
    frag = F.get(fid)
    assert delta == pytest.approx(-0.2) and frag.confidence == 0.5
    assert (frag.helped_count, frag.hurt_count, frag.evidence_at, frag.pinned) == (0, 0, None, False)
    assert [r["source"] for r in _rows()] == ["shadow:explicit"]


def test_access_behaviour_continues_and_signals_are_recorded(shadow):
    T, F, sessions, _ = shadow
    fid = T.remember(content="deploy via the acme pipeline")["fragment"]["id"]
    T.log_progress(kind="ask", content="how to deploy")
    T.recall(query="acme pipeline deploy")
    assert F.get(fid).confidence == pytest.approx(0.5 + 0.015 * 0.5)
    T.log_progress(kind="done", content=f"used {fid}")
    T.log_progress(kind="ask", content="still broken")
    T.end_progress()
    kinds = {r["source"] for r in _rows()}
    assert {"shadow:done_cite", "shadow:settle"} <= kinds
    assert any(k.startswith("shadow:correction:") for k in kinds)
    frag = F.get(fid)
    assert (frag.helped_count, frag.hurt_count, frag.evidence_at) == (0, 0, None)
    assert frag.confidence > 0.5


def test_mark_and_forget_record_without_evidence_effect(shadow):
    T, F, _, _ = shadow
    fid = T.remember(content="mark me")["fragment"]["id"]
    T.mark(fid, useful=True)
    assert F.get(fid).confidence == 1.0
    T.forget(fid, reason="wrong")
    sources = {r["source"] for r in _rows()}
    assert {"shadow:mark_useful", "shadow:explicit"} <= sources
    assert F.get(fid).helped_count == 0


def test_negation_records_medium_hurt_once(shadow):
    T, F, sessions, _ = shadow
    from hippocampus.dynamics import boost as boost_dyn, negation

    fid = F.create("recalled").id
    sid = sessions.current_session_id("pytest")
    boost_dyn.boost(fid, session_id=sid)
    negation.infer_and_forget("no, that's wrong", session_id=sid)
    rows = [r for r in _rows() if r["fragment_id"] == fid]
    assert [(r["source"], r["strength"]) for r in rows] == [("shadow:negation", "medium")]


def test_switch_to_evidence_does_not_replay_shadow(shadow):
    T, F, sessions, mp = shadow
    fid = T.remember(content="switch fact")["fragment"]["id"]
    T.log_progress(kind="ask", content="q")
    T.recall(query="switch fact")
    T.log_progress(kind="done", content="d")
    T.end_progress()
    settled_conf = F.get(fid).confidence
    shadow_rows = len(_rows())
    mp.setenv("HIPPO_CONFIDENCE_MODEL", "evidence")
    from hippocampus.dynamics import outcomes

    assert outcomes.settle_pending() == 0
    assert F.get(fid).confidence == settled_conf and len(_rows()) == shadow_rows
    assert all(r["source"].startswith("shadow:") for r in _rows())


def test_shadow_syncs_nothing_new(shadow):
    T, F, _, _ = shadow
    from hippocampus.sync import client

    fid = T.remember(content="sync quiet")["fragment"]["id"]
    since = F.get(fid).updated_at
    T.mark(fid, useful=False)
    assert F.get(fid).evidence_at is None
    assert all(o["payload"].get("evidence_at") is None for o in client.collect_ops(since))


def test_report_summarises(shadow):
    T, F, _, _ = shadow
    from hippocampus.dynamics import outcome_report

    fid = T.remember(content="report me")["fragment"]["id"]
    T.log_progress(kind="ask", content="q")
    T.recall(query="report me")
    T.log_progress(kind="ask", content="still broken")
    out = outcome_report.build()
    assert any(c["source"] == "correction" and c["shadow"] for c in out["counts"])
    assert out["top_fragments"][0]["id"] == fid
    assert out["recent_corrections"] == ["still broken"]


def test_mark_settled_skips_history_and_shadow_failure_is_soft(shadow, monkeypatch):
    T, F, sessions, mp = shadow
    from click.testing import CliRunner

    from hippocampus.cli.main import cli
    from hippocampus.dynamics import outcomes

    sid = sessions.current_session_id("pytest")
    fid = F.create("hist").id
    sessions.log_access(sid, fid)
    sessions.close_session(sid)
    out = CliRunner().invoke(cli, ["confidence", "mark-settled"])
    assert '"marked": 1' in out.output
    assert outcomes.settle_pending() == 0 and _rows() == []

    def boom(*a, **k):
        raise RuntimeError("shadow down")

    mp.setattr(outcomes, "apply", boom)
    assert T.mark(fid, useful=True)["found"] is True
    assert F.get(fid).confidence == 1.0
