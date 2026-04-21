"""End-to-end: full biology cycle — remember, recall-boost, shield, decay, archive."""

from __future__ import annotations

import pytest


def test_full_cycle_shield_then_decay_then_archive(hippo_env):
    """Walk through the biological life of a fragment."""
    from hippocampus import config
    from hippocampus.dynamics import archive, decay
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F, sessions

    # 1. Remember
    res = T.remember(content="Kafka consumers must be idempotent.", summary="kafka")
    fid = res["fragment"]["id"]
    assert F.get(fid).confidence == 0.5

    # 2. Recall → boost applied (+0.015)
    T.recall(query="kafka")
    assert F.get(fid).confidence == pytest.approx(0.515)

    # 3. New session starts — fragment is still in the recent-two-sessions
    #    shield window, so decay must NOT touch it.
    current_sid = sessions.current_session_id("pytest")
    sessions.close_session(current_sid)
    sessions.open_session("pytest")
    decay.run_decay_cycle()
    assert F.get(fid).confidence == pytest.approx(0.515)

    # 4. Two more sessions pass without access. The shield window slides past
    #    our fragment and decay starts biting.
    for _ in range(2):
        sid = sessions.current_session_id("pytest")
        sessions.close_session(sid)
        sessions.open_session("pytest")
    decay.run_decay_cycle()
    assert F.get(fid).confidence == pytest.approx(0.513)

    # 5. Drop confidence near zero and simulate the grace period having
    #    elapsed so the archive sweep catches it.
    F.update_fields(
        fid,
        confidence=0.04,
        below_threshold_since="2020-01-01T00:00:00.000Z",  # deep past
    )
    result = archive.run_archive_cycle()
    assert result.fragments_archived == 1
    assert F.get(fid) is None

    # 6. Mirror was moved to archive dir, not deleted.
    archive_path = hippo_env["fragments_dir"] / ".archive" / f"{fid}.md"
    assert archive_path.exists()


def test_pin_blocks_decay_forever(hippo_env):
    from hippocampus.dynamics import decay
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    r = T.remember(content="Pinned truth.", summary="truth", pinned=True)
    fid = r["fragment"]["id"]

    # Run many decay cycles — pinned fragment doesn't budge.
    for _ in range(20):
        decay.run_decay_cycle()

    assert F.get(fid).confidence == 0.5
