"""Association edge store.

Associations are undirected. We enforce a canonical ordering (fragment_a <
fragment_b as text) so (A,B) and (B,A) collapse to a single row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from hippocampus.storage.db import get_conn, get_ro_conn


def _canonical(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def strengthen(a: str, b: str, weight_delta: float = 1.0) -> None:
    """Increment co-access count and weight for pair (a, b). Idempotent."""
    if a == b:
        return
    lo, hi = _canonical(a, b)
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO associations (fragment_a, fragment_b, weight, co_accessed_count, last_co_accessed_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(fragment_a, fragment_b) DO UPDATE SET
                weight = weight + excluded.weight,
                co_accessed_count = co_accessed_count + 1,
                last_co_accessed_at = excluded.last_co_accessed_at
            """,
            (lo, hi, weight_delta, now),
        )


def strengthen_all(fragment_ids: Iterable[str]) -> int:
    """Strengthen every pair in the given list. Returns number of edges touched."""
    ids = list(dict.fromkeys(fragment_ids))  # dedupe, keep order
    if len(ids) < 2:
        return 0
    touched = 0
    now = _now()
    with get_conn() as conn:
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                lo, hi = _canonical(a, b)
                conn.execute(
                    """
                    INSERT INTO associations (fragment_a, fragment_b, weight, co_accessed_count, last_co_accessed_at)
                    VALUES (?, ?, 1.0, 1, ?)
                    ON CONFLICT(fragment_a, fragment_b) DO UPDATE SET
                        weight = weight + 1.0,
                        co_accessed_count = co_accessed_count + 1,
                        last_co_accessed_at = excluded.last_co_accessed_at
                    """,
                    (lo, hi, now),
                )
                touched += 1
    return touched


def get_associated(fragment_id: str, limit: int = 10) -> list[tuple[str, float, int]]:
    """Return [(other_id, weight, co_accessed_count)] sorted by weight desc."""
    with get_ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                CASE WHEN fragment_a = ? THEN fragment_b ELSE fragment_a END AS other,
                weight,
                co_accessed_count
            FROM associations
            WHERE fragment_a = ? OR fragment_b = ?
            ORDER BY weight DESC
            LIMIT ?
            """,
            (fragment_id, fragment_id, fragment_id, limit),
        ).fetchall()
    return [(r["other"], float(r["weight"]), int(r["co_accessed_count"])) for r in rows]
