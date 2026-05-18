"""V9 W1-4 — decay recency-skip shield.

When `decay_skip_recent_days > 0`, any fragment whose `last_accessed_at` falls
inside that window is treated as if it were pinned for the purposes of decay.
This fixes the 16:1 decay/boost imbalance documented in the V9 PRD audit.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_recent_access_shields_from_decay(hippo_env, monkeypatch):
    from hippocampus.dynamics import decay
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F, sessions

    monkeypatch.setenv("HIPPO_DECAY_SKIP_RECENT_DAYS", "30")

    res = T.remember(content="Recent knowledge.", summary="recent")
    fid = res["fragment"]["id"]

    # Drop into a brand-new session so the session-shield doesn't help us.
    for _ in range(3):
        sid = sessions.current_session_id("pytest")
        sessions.close_session(sid)
        sessions.open_session("pytest")

    # `last_accessed_at` is None (no recall yet), so the recency shield does
    # NOT kick in — fragment decays normally.
    before = F.get(fid).confidence
    result = decay.run_decay_cycle()
    after = F.get(fid).confidence
    assert after < before
    assert result.fragments_recency_skipped == 0


def test_recency_shield_blocks_decay_when_accessed_today(hippo_env, monkeypatch):
    from hippocampus.dynamics import decay
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F, sessions

    monkeypatch.setenv("HIPPO_DECAY_SKIP_RECENT_DAYS", "30")

    res = T.remember(content="Recent knowledge.", summary="recent")
    fid = res["fragment"]["id"]

    # Stamp last_accessed_at to today and slide outside the session shield.
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    F.update_fields(fid, last_accessed_at=today_iso)
    for _ in range(3):
        sid = sessions.current_session_id("pytest")
        sessions.close_session(sid)
        sessions.open_session("pytest")

    before = F.get(fid).confidence
    result = decay.run_decay_cycle()
    after = F.get(fid).confidence
    assert after == before
    assert result.fragments_recency_skipped == 1


def test_recency_shield_off_when_zero(hippo_env, monkeypatch):
    """decay_skip_recent_days=0 disables the new shield entirely."""
    from hippocampus.dynamics import decay
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F, sessions

    monkeypatch.setenv("HIPPO_DECAY_SKIP_RECENT_DAYS", "0")

    res = T.remember(content="Old + accessed knowledge.", summary="old")
    fid = res["fragment"]["id"]

    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    F.update_fields(fid, last_accessed_at=today_iso)
    for _ in range(3):
        sid = sessions.current_session_id("pytest")
        sessions.close_session(sid)
        sessions.open_session("pytest")

    before = F.get(fid).confidence
    result = decay.run_decay_cycle()
    after = F.get(fid).confidence
    assert after < before
    assert result.fragments_recency_skipped == 0


def test_pinned_takes_precedence_over_recency(hippo_env, monkeypatch):
    """Pinned shield is its own bucket — don't accidentally lump them together."""
    from hippocampus.dynamics import decay
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    monkeypatch.setenv("HIPPO_DECAY_SKIP_RECENT_DAYS", "30")
    res = T.remember(content="Pinned + recent.", summary="pinned", pinned=True)
    fid = res["fragment"]["id"]
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    F.update_fields(fid, last_accessed_at=today_iso)

    result = decay.run_decay_cycle()
    assert result.fragments_pinned_skipped >= 1
    # The same fragment shouldn't be counted twice.
    assert result.fragments_recency_skipped == 0
