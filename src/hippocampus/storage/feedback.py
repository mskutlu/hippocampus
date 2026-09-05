"""Feedback log — audit trail for every confidence-changing event."""

from __future__ import annotations

from hippocampus.storage.db import get_conn, get_ro_conn


def log(
    fragment_id: str,
    kind: str,
    delta: float | None = None,
    reason: str | None = None,
    session_id: str | None = None,
) -> None:
    """Write one event to the feedback log.

    kind ∈ {'boost', 'decay', 'negative', 'pin', 'unpin', 'archive'}
    delta is the confidence change (signed) when applicable.
    """
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO feedback_log (fragment_id, session_id, kind, delta, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (fragment_id, session_id, kind, delta, reason),
        )


def prune(days: int) -> int:
    """Delete feedback rows older than `days`. Returns rows removed."""
    if days < 1:
        return 0
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM feedback_log WHERE created_at < strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)",
            (f"-{int(days)} days",),
        )
        return cur.rowcount


def recent(limit: int = 50) -> list[dict]:
    with get_ro_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM feedback_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
