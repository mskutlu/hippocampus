"""Unit tests for ranking (recency factor + composite score + top_n)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_recency_zero_when_never_accessed(hippo_env):
    from hippocampus.dynamics import ranking

    assert ranking.recency_factor(None) == 0.0


def test_recency_approaches_one_for_very_recent(hippo_env):
    from hippocampus.dynamics import ranking

    now = datetime.now(timezone.utc)
    ts = _iso(now - timedelta(minutes=1))
    r = ranking.recency_factor(ts)
    assert 0.99 < r <= 1.0


def test_recency_decays_over_halflife(hippo_env):
    from hippocampus.dynamics import ranking
    from hippocampus import config

    now = datetime.now(timezone.utc)
    ts = _iso(now - timedelta(days=config.RECENCY_HALFLIFE_DAYS))
    r = ranking.recency_factor(ts, now=now)
    # exp(-14/14) = exp(-1) ≈ 0.3679
    assert r == pytest.approx(0.3679, rel=0.01)


def test_top_n_uses_score_before_pin_bonus(hippo_env):
    from hippocampus.storage import fragments as F
    from hippocampus.dynamics import boost, ranking

    a = F.create("a", summary="A")
    b = F.create("b", summary="B")
    c = F.create("c", summary="C")

    # Boost a enough to beat the small pin bonus on b. Leave c untouched.
    for _ in range(4):
        boost.boost(a.id, client="pytest")
    F.update_fields(b.id, pinned=True)

    top = ranking.top_n(limit=10)
    ids = [t.id for t in top]
    assert ids.index(a.id) < ids.index(b.id)
    assert ids.index(b.id) < ids.index(c.id)
