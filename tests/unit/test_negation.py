"""V9 W6 — negation inference."""

from __future__ import annotations


def test_negation_regex_recognises_common_forms(hippo_env):
    from hippocampus.dynamics import negation

    positives = [
        "no, that's not what I meant",
        "Nope, try again.",
        "wrong, the answer is X",
        "actually, no — the right call is Y",
        "hayır, böyle değil",
        "yanlış cevap verdin",
        "that's not right",
        "that is not what happened",
    ]
    for p in positives:
        assert negation.looks_like_negation(p), p

    negatives = [
        "yes, do that",
        "now run the build",
        "are you sure?",
        "the answer is no",  # "no" not at start
    ]
    for p in negatives:
        assert not negation.looks_like_negation(p), p


def test_infer_and_forget_demotes_recent_boost(hippo_env, monkeypatch):
    from hippocampus.dynamics import boost as boost_dyn
    from hippocampus.dynamics import negation
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F, sessions

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    monkeypatch.setenv("HIPPO_INFERRED_NEGATION_ENABLED", "true")

    rmem = T.remember(content="The acme-orders service deploys via GitLab CI.")
    fid = rmem["fragment"]["id"]
    sid = sessions.current_session_id("devin")

    # Simulate a log_progress_auto boost (typical AI mention path)
    boost_dyn.boost(fid, context_tag="log_progress_auto:done", session_id=sid, client="devin")
    T.log_progress(kind="done", content="Boosted acme-orders knowledge.")  # opens session if needed

    before = F.get(fid).confidence

    # User pushes back
    demoted = negation.infer_and_forget("no, that's not how acme-orders deploys.", session_id=sid)
    assert demoted == fid
    after = F.get(fid).confidence
    assert after < before


def test_infer_off_when_disabled(hippo_env, monkeypatch):
    from hippocampus.dynamics import boost as boost_dyn
    from hippocampus.dynamics import negation
    from hippocampus.mcp import tools as T
    from hippocampus.storage import sessions

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    monkeypatch.setenv("HIPPO_INFERRED_NEGATION_ENABLED", "false")
    rmem = T.remember(content="x")
    sid = sessions.current_session_id("devin")
    boost_dyn.boost(rmem["fragment"]["id"], context_tag="log_progress_auto:done", session_id=sid)
    T.log_progress(kind="done", content="Boosted x.")

    out = negation.infer_and_forget("no, that's wrong", session_id=sid)
    assert out is None


def test_log_progress_returns_demoted_id(hippo_env, monkeypatch):
    """End-to-end: when log_progress(kind='ask') sees a negation, it demotes."""
    from hippocampus.dynamics import boost as boost_dyn
    from hippocampus.mcp import tools as T
    from hippocampus.storage import sessions

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    monkeypatch.setenv("HIPPO_INFERRED_NEGATION_ENABLED", "true")
    monkeypatch.setenv("HIPPO_LOG_PROGRESS_RECALL_BOOST_K", "0")  # isolate

    rmem = T.remember(content="The acme-orders service deploys via GitLab CI.")
    fid = rmem["fragment"]["id"]
    sid = sessions.current_session_id("devin")
    boost_dyn.boost(fid, context_tag="log_progress_auto:done", session_id=sid, client="devin")
    T.log_progress(kind="done", content="Mentioned acme-orders deployment.")

    out = T.log_progress(kind="ask", content="no, that's not how acme-orders works.")
    assert out["demoted_fragment_id"] == fid
