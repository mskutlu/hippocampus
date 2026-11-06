"""Peer turn: a session has peer message CONTENT in its context.

Begun by `inbox()` returning content; ended by the Claude Code Stop hook or by
`a2a_peer_turn_ttl_s`. Busy: a Claude Code user turn (UserPromptSubmit .. Stop, or idle `a2a_busy_idle_s`); channel pushes wait. Read by the MCP gate (a2a.tools.peer_gate) and the
PreToolUse guard (a2a.guard, read-only and stdlib-only).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hippocampus import a2a
from hippocampus.a2a import store


def begin_peer_turn(session_key: str, msg: dict[str, Any], path: Path | None = None, trust: str = "unknown") -> None:
    """`trust` (channel.trust_for) is stored so the hook-side guard can word its deny without a network call."""
    sender = msg.get("from") or msg.get("sender") or ""
    with store.connect(path or store.db_path()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO a2a_peer_turn(session_key, msg_id, sender, thread_id, hop, started_at, trust) VALUES (?,?,?,?,?,?,?)",
            (session_key, msg["msg_id"], sender, msg["thread_id"], int(msg.get("hop") or 0), store._now(), trust),
        )


def end_peer_turn(session_key: str, path: Path | None = None) -> None:
    with store.connect(path or store.db_path()) as conn:
        conn.execute("DELETE FROM a2a_peer_turn WHERE session_key = ?", (session_key,))


def active_peer_turn(session_key: str, path: Path | None = None) -> dict[str, Any] | None:
    with store.connect(path or store.db_path()) as conn:
        row = conn.execute("SELECT * FROM a2a_peer_turn WHERE session_key = ?", (session_key,)).fetchone()
        if row and not a2a.turn_fresh(row["started_at"]):
            conn.execute("DELETE FROM a2a_peer_turn WHERE session_key = ?", (session_key,))
            row = None
    return dict(row) if row else None


def mark_busy(session_key: str, path: Path | None = None) -> None:
    """UserPromptSubmit: a user turn is running in this Claude Code session."""
    with store.connect(path or store.db_path()) as conn:
        conn.execute("INSERT OR REPLACE INTO a2a_busy(session_key, started_at) VALUES (?, ?)", (session_key, store._now()))


def end_turn(session_key: str, path: Path | None = None) -> None:
    """Stop: the agent turn (user or peer) is over."""
    db = path or store.db_path()
    with store.connect(db) as conn:
        conn.execute("DELETE FROM a2a_busy WHERE session_key = ?", (session_key,))
        conn.execute("DELETE FROM a2a_peer_turn WHERE session_key = ?", (session_key,))
    a2a.activity_file(db, session_key).unlink(missing_ok=True)


def busy(session_key: str, path: Path | None = None) -> bool:
    """A user turn or a peer turn is in progress and not idle (`a2a.turn_active`: an Esc'd turn whose Stop never ran
    lapses after `a2a_busy_idle_s` without tool calls, any turn after `a2a_peer_turn_ttl_s`). Push gating only:
    the guard's lockdown uses `active_peer_turn` / `guard.live_turns`, which ignore the idle rule."""
    db = path or store.db_path()
    with store.connect(db) as conn:
        rows = conn.execute(
            "SELECT started_at FROM a2a_busy WHERE session_key = ? UNION ALL SELECT started_at FROM a2a_peer_turn WHERE session_key = ?",
            (session_key, session_key),
        ).fetchall()
    return any(a2a.turn_active(r["started_at"], db, session_key) for r in rows)
