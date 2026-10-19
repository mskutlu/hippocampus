"""V9 W6 — negation inference."""

from __future__ import annotations

import pytest


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


def test_negation_only_targets_current_session(hippo_env, monkeypatch):
    from hippocampus.dynamics import boost as boost_dyn
    from hippocampus.dynamics import negation
    from hippocampus.storage import fragments as F, sessions

    monkeypatch.setenv("HIPPO_INFERRED_NEGATION_ENABLED", "true")

    first = F.create("First session memory")
    second = F.create("Second session memory")
    first_session = sessions.open_session("devin", session_key="first")
    second_session = sessions.open_session("devin", session_key="second")
    boost_dyn.boost(
        first.id,
        context_tag="recall:first",
        session_id=first_session,
    )
    boost_dyn.boost(
        second.id,
        context_tag="recall:second",
        session_id=second_session,
    )

    demoted = negation.infer_and_forget(
        "no, that is wrong",
        session_id=first_session,
    )

    assert demoted == first.id
    assert F.get(first.id).confidence < F.get(second.id).confidence


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("i am still getting the same error after the deploy", True),
        ("the page still doesnt work like i asked", True),
        ("User still sees the stale value after retry; check the cache", True),
        ("the fix did not work, the button is still dead", True),
        ("that change doesn't fix it for me, same result again", True),
        ("the sidebar button doesnt work on the settings page", False),
        ("the dropdown doesnt work as expected and the theme looks off", False),
        ("the tab control does not work properly, it should expand", False),
        ("if you switched it to prod revert it to dev please", False),
        ("it's still broken on staging", True),
        ("please revert that, it made things worse", True),
        ("wrong value again, expected the other one", True),
        ("hala çalışmıyor", True),
        ("bu da olmadı", True),
        ("düzelmedi, aynı hata", True),
        ("sorun hâlâ duruyor", True),
        ("dont broke anything", False),
        ("can you check it again, i may revert some code first", False),
        ("try again i reset the vpn", False),
        ("does it still work after the change?", False),
        ("make sure it is amd64 not arm64", False),
        ("looks good, merge it", False),
        ("<task-notification> still failing in the log", False),
    ],
)
def test_correction_table(prompt, expected):
    from hippocampus.dynamics import negation

    assert negation.looks_like_correction(prompt) is expected


def test_evidence_negation_targets_recalled_not_cluster(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPO_CONFIDENCE_MODEL", "evidence")
    monkeypatch.setenv("HIPPO_INFERRED_NEGATION_ENABLED", "true")
    from hippocampus.dynamics import boost as boost_dyn
    from hippocampus.dynamics import negation
    from hippocampus.storage import feedback, fragments as F, sessions

    recalled = F.create("recalled fact").id
    neighbour = F.create("cluster neighbour").id
    sid = sessions.current_session_id("pytest")
    boost_dyn.boost(recalled, session_id=sid)
    feedback.log(neighbour, "boost", delta=0.001, reason=f"cluster:{recalled}", session_id=sid)

    assert negation.infer_and_forget("no, that's wrong", session_id=sid) == recalled
    assert F.get(recalled).hurt_count == 1 and F.get(recalled).confidence == pytest.approx(0.42)
    assert F.get(neighbour).confidence == 0.5


def test_evidence_negation_ignores_auto_and_inject(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPO_CONFIDENCE_MODEL", "evidence")
    monkeypatch.setenv("HIPPO_INFERRED_NEGATION_ENABLED", "true")
    from hippocampus.dynamics import negation
    from hippocampus.storage import fragments as F, sessions

    fid = F.create("only injected").id
    sid = sessions.current_session_id("pytest")
    sessions.log_access(sid, fid, via="inject")
    assert negation.infer_and_forget("no, wrong", session_id=sid) is None
