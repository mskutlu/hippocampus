"""Evidence model end to end: inject, recall, done, close; corrections; sync."""

from __future__ import annotations

import pytest


@pytest.fixture
def env(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPO_LOG_PROGRESS_RECALL_BOOST_K", "0")
    monkeypatch.setenv("HIPPO_INFERRED_NEGATION_ENABLED", "true")
    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F, sessions

    return T, F, sessions


def _conf(F, fid):
    return F.get(fid).confidence


def test_recalled_helped_injected_neutral(env, monkeypatch):
    monkeypatch.setenv("HIPPO_CONFIDENCE_MODEL", "evidence")
    T, F, sessions = env
    recalled = T.remember(content="deploys go through the acme pipeline")["fragment"]["id"]
    injected = T.remember(content="unrelated injected rule about zebras")["fragment"]["id"]
    sid = sessions.current_session_id("devin")
    sessions.log_access(sid, injected, via="inject")

    T.log_progress(kind="ask", content="how do deploys work")
    T.recall(query="acme pipeline deploys")
    T.log_progress(kind="done", content="explained deploys")
    T.end_progress()

    assert _conf(F, recalled) > 0.5 and _conf(F, injected) == 0.5


def test_correction_after_done_hurts_recalled(env, monkeypatch):
    monkeypatch.setenv("HIPPO_CONFIDENCE_MODEL", "evidence")
    T, F, sessions = env
    fid = T.remember(content="restart the acme worker with systemctl")["fragment"]["id"]
    T.log_progress(kind="ask", content="restart the worker")
    T.recall(query="acme worker systemctl")
    T.log_progress(kind="done", content="restarted it")
    out = T.log_progress(kind="ask", content="still broken")
    assert out["demoted_fragment_id"] is None
    frag = F.get(fid)
    assert frag.confidence == pytest.approx(0.46) and frag.hurt_count == 1


def test_cited_done_is_medium_helped(env, monkeypatch):
    monkeypatch.setenv("HIPPO_CONFIDENCE_MODEL", "evidence")
    T, F, _ = env
    fid = T.remember(content="cite me")["fragment"]["id"]
    T.log_progress(kind="done", content=f"used {fid} to finish")
    frag = F.get(fid)
    assert frag.helped_count == 1 and frag.confidence == pytest.approx(0.525)


def test_access_mode_is_unchanged(env):
    T, F, sessions = env
    fid = T.remember(content="plain access behaviour")["fragment"]["id"]
    T.log_progress(kind="ask", content="q")
    T.recall(query="plain access behaviour")
    T.log_progress(kind="done", content="d")
    T.log_progress(kind="ask", content="still broken")
    T.end_progress()
    frag = F.get(fid)
    assert frag.confidence == pytest.approx(0.5 + 0.015 * 0.5)
    assert (frag.helped_count, frag.hurt_count, frag.evidence_at) == (0, 0, None)
