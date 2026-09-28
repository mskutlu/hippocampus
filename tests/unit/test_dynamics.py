"""Unit tests for biological dynamics (boost / decay / shield / negative feedback)."""

from __future__ import annotations

import json

import pytest


def test_boost_increments_confidence_and_accessed(hippo_env):
    from hippocampus.storage import fragments as F
    from hippocampus.dynamics import boost

    frag = F.create("some content", summary="s")
    assert frag.confidence == 0.5
    assert frag.accessed == 0

    boost.boost(frag.id, client="pytest")

    after = F.get(frag.id)
    assert after.confidence == pytest.approx(0.5075)  # 0.5 + 0.015 * (1 - 0.5)
    assert after.accessed == 1
    assert after.last_accessed_at is not None


def test_boost_is_asymptotic_below_one(hippo_env):
    from hippocampus.storage import fragments as F
    from hippocampus.dynamics import boost

    frag = F.create("c", summary="s")
    F.update_fields(frag.id, confidence=0.99)
    boost.boost(frag.id, client="pytest")
    after = F.get(frag.id)
    assert 0.99 < after.confidence < 1.0
    assert after.confidence == pytest.approx(0.99 + 0.015 * 0.01)


def test_mark_useful_reaches_one(hippo_env):
    from hippocampus.storage import fragments as F
    from hippocampus.dynamics import boost

    frag = F.create("c", summary="s")
    boost.mark_useful(frag.id)
    after = F.get(frag.id)
    assert after.confidence == 1.0
    assert after.accessed == 1


def test_injected_fragment_is_not_boosted(hippo_env):
    from hippocampus.storage import fragments as F, sessions
    from hippocampus.dynamics import boost

    sid = sessions.open_session("pytest")
    frag = F.create("c", summary="s")
    sessions.log_access(sid, frag.id, via="inject")
    boost.boost(frag.id, session_id=sid)
    assert F.get(frag.id).confidence == 0.5
    assert F.get(frag.id).accessed == 0

    # A real recall in a later session boosts again.
    sessions.close_session(sid)
    sid2 = sessions.open_session("pytest")
    boost.boost(frag.id, session_id=sid2)
    assert F.get(frag.id).confidence == pytest.approx(0.5075)


def test_boost_logs_context_tag_without_tagging(hippo_env):
    from hippocampus.storage import feedback, fragments as F
    from hippocampus.dynamics import boost

    frag = F.create("c", summary="s", tags=["kafka"])
    boost.boost(frag.id, context_tag="debugging", client="pytest")
    assert F.get(frag.id).tags == ["kafka"]
    assert feedback.recent(1)[0]["reason"] == "debugging"


def test_recall_with_context_tag_leaves_tags_unchanged(hippo_env):
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    fid = T.remember(content="kafka consumers must be idempotent", tags=["kafka"])["fragment"]["id"]
    out = T.recall(query="kafka idempotent", context_tag="blue-29-review")
    assert fid in [h["id"] for h in out["fragments"]]
    assert F.get(fid).tags == ["kafka"]


def test_tags_prune_removes_only_accreted_tags(hippo_env):
    from click.testing import CliRunner

    from hippocampus.cli.main import cli
    from hippocampus.storage import feedback, fragments as F
    from hippocampus.storage.db import get_conn

    frag = F.create("c", summary="s", tags=["kafka", "blue-29-review", "debugging"])
    with get_conn() as conn:
        conn.execute("INSERT INTO fragment_tags(fragment_id, tag) VALUES (?, 'log_progress_auto:ask')", (frag.id,))
    feedback.log(frag.id, "boost", delta=0.01, reason="blue-29-review")
    other = F.create("o", summary="s", tags=["blue-29-review"])

    dry = json.loads(CliRunner().invoke(cli, ["tags", "prune"]).output)
    assert dry["dry_run"] is True and dry["tag_assignments"] == 2
    assert dry["by_reason"] == {"boost-provenance": 1, "pipeline-prefix": 1}
    assert len(F.get(frag.id).tags) == 4

    applied = json.loads(CliRunner().invoke(cli, ["tags", "prune", "--apply"]).output)
    assert applied["backup"]
    assert sorted(F.get(frag.id).tags) == ["debugging", "kafka"]
    assert F.get(other.id).tags == ["blue-29-review"]
    assert (hippo_env["fragments_dir"] / f"{frag.id}.md").read_text().count("blue-29-review") == 0


def test_boost_many_creates_associations(hippo_env):
    from hippocampus.storage import associations, fragments as F
    from hippocampus.dynamics import boost

    a = F.create("a content", summary="a")
    b = F.create("b content", summary="b")
    c = F.create("c content", summary="c")

    boost.boost_many([a.id, b.id, c.id], client="pytest")

    a_assoc = {other for other, _, _ in associations.get_associated(a.id)}
    assert b.id in a_assoc
    assert c.id in a_assoc


def test_negative_feedback_reduces_confidence(hippo_env):
    from hippocampus.storage import fragments as F
    from hippocampus.dynamics import boost

    frag = F.create("c", summary="s")
    before = frag.confidence
    boost.apply_negative_feedback(frag.id, reason="wrong")
    after = F.get(frag.id)
    assert after.confidence == pytest.approx(before - 0.02)


def test_negative_feedback_floors_at_zero(hippo_env):
    from hippocampus.storage import fragments as F
    from hippocampus.dynamics import boost

    frag = F.create("c", summary="s")
    F.update_fields(frag.id, confidence=0.01)
    boost.apply_negative_feedback(frag.id)
    after = F.get(frag.id)
    assert after.confidence == 0.0


def test_decay_ignores_pinned(hippo_env):
    from hippocampus.storage import fragments as F
    from hippocampus.dynamics import decay

    frag = F.create("c", summary="s", pinned=True)
    # Nothing in any session → would decay if not pinned
    result = decay.run_decay_cycle()
    after = F.get(frag.id)
    assert after.confidence == 0.5
    assert result.fragments_pinned_skipped == 1


def test_decay_shields_recently_accessed(hippo_env):
    from hippocampus.storage import fragments as F, sessions
    from hippocampus.dynamics import boost, decay

    sid = sessions.open_session("pytest")
    f = F.create("c", summary="s")
    boost.boost(f.id, session_id=sid)
    sessions.close_session(sid)

    # Open another session — the fragment was accessed in the immediately
    # previous session, so it must still be shielded.
    sessions.open_session("pytest")
    decay.run_decay_cycle()
    after = F.get(f.id)
    assert after.confidence == pytest.approx(0.5075)  # boosted, not decayed


def test_decay_applies_to_unused_fragments(hippo_env):
    from hippocampus.storage import fragments as F, sessions
    from hippocampus.dynamics import decay

    f = F.create("c", summary="s")
    # No session, no access → goes straight to decay
    decay.run_decay_cycle()
    after = F.get(f.id)
    assert after.confidence == pytest.approx(0.498)


def test_decay_floors_at_zero(hippo_env):
    from hippocampus.storage import fragments as F
    from hippocampus.dynamics import decay

    f = F.create("c", summary="s")
    F.update_fields(f.id, confidence=0.0)
    decay.run_decay_cycle()
    after = F.get(f.id)
    assert after.confidence == 0.0


def test_decay_flags_below_threshold(hippo_env):
    from hippocampus.storage import fragments as F
    from hippocampus.dynamics import decay
    from hippocampus import config

    f = F.create("c", summary="s")
    F.update_fields(f.id, confidence=config.ARCHIVE_THRESHOLD + 0.001)
    decay.run_decay_cycle()
    after = F.get(f.id)
    assert after.confidence < config.ARCHIVE_THRESHOLD
    assert after.below_threshold_since is not None
