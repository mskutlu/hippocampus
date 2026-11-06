"""Peer turn: a session has peer message CONTENT in its context.

Begun by `inbox()` returning content; ended by the Claude Code Stop hook or by
`a2a_peer_turn_ttl_s`. Read by the MCP gate (a2a.tools.peer_gate) and the
PreToolUse guard (a2a.guard, read-only and stdlib-only).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hippocampus import a2a
from hippocampus.a2a import store


def begin_peer_turn(session_key: str, msg: dict[str, Any], path: Path | None = None) -> None:
    sender = msg.get("from") or msg.get("sender") or ""
    with store.connect(path or store.db_path()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO a2a_peer_turn(session_key, msg_id, sender, thread_id, hop, started_at) VALUES (?,?,?,?,?,?)",
            (session_key, msg["msg_id"], sender, msg["thread_id"], int(msg.get("hop") or 0), store._now()),
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
