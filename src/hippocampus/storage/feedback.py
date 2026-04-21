"""Feedback log — audit trail for every confidence-changing event."""

from __future__ import annotations

from hippocampus.storage.db import get_conn, get_ro_conn


def log(fragment_id: str, kind: str, delta: float | None = None, reason: str | None = None) -> None:
    """Write one event to the feedback log.

    kind ∈ {'boost', 'decay', 'negative', 'pin', 'unpin', 'archive'}
    delta is the confidence change (signed) when applicable.
    """
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO feedback_log (fragment_id, kind, delta, reason)
            VALUES (?, ?, ?, ?)
            """,
            (fragment_id, kind, delta, reason),
        )


def recent(limit: int = 50) -> list[dict]:
    with get_ro_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM feedback_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
