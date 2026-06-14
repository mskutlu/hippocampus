"""Ranking for injection.

Each fragment is scored with
    score = confidence * RANK_W_CONFIDENCE + recency * RANK_W_RECENCY
where
    recency = exp(-days_since_last_access / RECENCY_HALFLIFE_DAYS)

The top-N list is produced without touching confidence (no boost on ranking).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from hippocampus import config
from hippocampus.storage import fragments as frag_store


def _parse_utc(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def recency_factor(last_accessed_at: str | None, now: datetime | None = None) -> float:
    """Return recency weight in [0, 1]. 1 = just accessed, →0 = long ago."""
    now = now or datetime.now(timezone.utc)
    dt = _parse_utc(last_accessed_at)
    if dt is None:
        # Never accessed → treat recency as 0.
        return 0.0
    days = max(0.0, (now - dt).total_seconds() / 86400.0)
    return math.exp(-days / config.RECENCY_HALFLIFE_DAYS)


def compute_score(confidence: float, last_accessed_at: str | None, now: datetime | None = None) -> float:
    r = recency_factor(last_accessed_at, now)
    return confidence * config.RANK_W_CONFIDENCE + r * config.RANK_W_RECENCY


def ranked_score(frag: frag_store.Fragment, now: datetime | None = None) -> float:
    score = compute_score(frag.confidence, frag.last_accessed_at, now)
    if frag.pinned:
        score += float(config.get_setting("pin_rank_bonus") or 0.0)
    return min(1.0, score)


def top_n(limit: int | None = None, min_confidence: float = 0.0) -> list[frag_store.Fragment]:
    """Return highest-scoring fragments for injection. Pinned always included first."""
    n = limit if limit is not None else config.TOP_N_DEFAULT
    now = datetime.now(timezone.utc)

    # Fetch a generous candidate pool; then rank in Python so we can apply the
    # full score (SQLite's math functions are patchy across builds).
    pool = frag_store.list_all(min_confidence=min_confidence, limit=max(200, n * 4))
    scored = [(ranked_score(f, now), f) for f in pool]

    # Score first. Pinned protects from decay and adds a small configurable
    # bonus, but no longer dominates the whole injection list.
    scored.sort(key=lambda t: (-t[0], -t[1].accessed, not t[1].pinned))
    return [f for _, f in scored[:n]]
