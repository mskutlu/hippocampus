"""Session ledger — per-session entries for working (short-term) memory.

Each entry is a single observation: the user asked, the AI did, a decision was
made, or a blocker came up. Entries are rendered into a marker-delimited
WORKING block in each client's rules file so the AI sees a compaction-safe
map of the current task on every turn.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from hippocampus.storage.db import get_conn, get_ro_conn

VALID_KINDS: frozenset[str] = frozenset(
    {"goal", "ask", "done", "blocker", "decision", "next", "note"}
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class LedgerEntry:
    id: int
    session_id: str
    client: str
    turn_index: int
    kind: str
    content: str
    details: str | None
    resolved: bool
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "client": self.client,
            "turn_index": self.turn_index,
            "kind": self.kind,
            "content": self.content,
            "details": self.details,
            "resolved": self.resolved,
            "created_at": self.created_at,
        }


def _row_to_entry(row: sqlite3.Row) -> LedgerEntry:
    return LedgerEntry(
        id=int(row["id"]),
        session_id=row["session_id"],
        client=row["client"],
        turn_index=int(row["turn_index"]),
        kind=row["kind"],
        content=row["content"],
        details=row["details"],
        resolved=bool(row["resolved"]),
        created_at=row["created_at"],
    )


def _next_turn_index(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(turn_index), 0) + 1 AS n FROM session_ledger WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return int(row["n"])


def _recent_duplicate(
    conn: sqlite3.Connection,
    session_id: str,
    kind: str,
    content: str,
    *,
    window_seconds: int = 60,
) -> bool:
    """Return True if an identical entry was logged within the last minute."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    row = conn.execute(
        """
        SELECT 1 FROM session_ledger
        WHERE session_id = ?
          AND kind = ?
          AND content = ?
          AND created_at >= ?
        LIMIT 1
        """,
        (session_id, kind, content, cutoff),
    ).fetchone()
    return row is not None


def log_entry(
    session_id: str,
    client: str,
    kind: str,
    content: str,
    details: str | None = None,
) -> LedgerEntry | None:
    """Append an entry. Returns None if deduped."""
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid kind {kind!r}; expected one of {sorted(VALID_KINDS)}")
    content = (content or "").strip()
    if not content:
        raise ValueError("content must be non-empty")

    with get_conn() as conn:
        if _recent_duplicate(conn, session_id, kind, content):
            return None
        turn = _next_turn_index(conn, session_id)
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO session_ledger
                (session_id, client, turn_index, kind, content, details, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, client, turn, kind, content, details, now),
        )
        entry_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM session_ledger WHERE id = ?", (entry_id,)
        ).fetchone()
    return _row_to_entry(row)


def current_entries(session_id: str) -> list[LedgerEntry]:
    """All entries for a single session, oldest first."""
    with get_ro_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM session_ledger WHERE session_id = ? ORDER BY turn_index ASC",
            (session_id,),
        ).fetchall()
    return [_row_to_entry(r) for r in rows]


def entries_by_client(client: str, limit: int = 200) -> list[LedgerEntry]:
    """All entries for a client, newest first — useful for historical views."""
    with get_ro_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM session_ledger WHERE client = ? ORDER BY created_at DESC LIMIT ?",
            (client, limit),
        ).fetchall()
    return [_row_to_entry(r) for r in rows]


def resolve(entry_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE session_ledger SET resolved = 1 WHERE id = ? AND resolved = 0",
            (entry_id,),
        )
        return cur.rowcount > 0


def delete_session_ledger(session_id: str) -> int:
    """Drop all entries for a session. Used by `hippo progress clear --confirm`."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM session_ledger WHERE session_id = ?", (session_id,))
        return cur.rowcount


def delete_last_entry(session_id: str) -> LedgerEntry | None:
    """Pop and return the most recent entry for a session, or None if empty."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM session_ledger WHERE session_id = ? ORDER BY turn_index DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        entry = _row_to_entry(row)
        conn.execute("DELETE FROM session_ledger WHERE id = ?", (entry.id,))
    return entry


def latest_session_across_clients() -> tuple[str, str, str] | None:
    """Return (session_id, client, started_at) for the most recent session, or None."""
    with get_ro_conn() as conn:
        row = conn.execute(
            """
            SELECT id, client, started_at FROM sessions
            WHERE ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    return row["id"], row["client"], row["started_at"]


def grouped_for_render(
    entries: Iterable[LedgerEntry],
    *,
    max_asks: int = 10,
    max_dones: int = 10,
    max_next: int = 5,
    max_notes: int = 5,
) -> dict:
    """Group entries into the buckets the renderer uses."""
    buckets: dict[str, list[LedgerEntry]] = {k: [] for k in VALID_KINDS}
    for e in entries:
        buckets[e.kind].append(e)

    # Most-recent-first where the renderer wants capped lists
    asks = list(reversed(buckets["ask"]))[:max_asks]
    dones = list(reversed(buckets["done"]))[:max_dones]
    nexts = list(reversed(buckets["next"]))[:max_next]
    notes = list(reversed(buckets["note"]))[:max_notes]

    # Goal: first explicit goal, else first ask if any
    goal = buckets["goal"][0] if buckets["goal"] else (buckets["ask"][0] if buckets["ask"] else None)

    # Blockers: only unresolved, oldest first (so they feel like a queue)
    blockers = [e for e in buckets["blocker"] if not e.resolved]

    # Decisions: all, oldest first
    decisions = list(buckets["decision"])

    return {
        "goal": goal,
        "asks": asks,
        "dones": dones,
        "blockers": blockers,
        "decisions": decisions,
        "nexts": nexts,
        "notes": notes,
        "turn_count": max((e.turn_index for e in entries), default=0),
    }
