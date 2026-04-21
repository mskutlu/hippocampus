"""Session + access-log bookkeeping.

Each AI client opens its own session on start-up (or implicitly on first
recall/remember). Access events are logged per session so the decay loop can
consult "was this fragment touched in the current or previous session?".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ulid import ULID

from hippocampus import config
from hippocampus.storage.db import get_conn, get_ro_conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _pointer_path(client: str):
    return config.SESSION_POINTER_DIR / f"{client}.id"


def open_session(client: str) -> str:
    """Open a new session for a client and persist its id to the pointer file."""
    client = client.strip().lower() or "unknown"
    config.ensure_dirs()
    sid = f"sess_{ULID()}"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (id, client, started_at) VALUES (?, ?, ?)",
            (sid, client, _now()),
        )
    _pointer_path(client).write_text(sid, encoding="utf-8")
    return sid


def rotate(client: str) -> str:
    """Close the current session for `client` (if any) and open a fresh one.

    Used by `end_progress` and whenever the AI client starts a new task. The
    previous session's ledger is preserved (the rows are kept) but stops
    appearing in the rendered WORKING block.
    """
    client_name = client.strip().lower() or "unknown"
    try:
        current = current_session_id(client_name, open_if_missing=False)
        close_session(current)
    except RuntimeError:
        pass
    return open_session(client_name)


def close_session(session_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (_now(), session_id),
        )
        return cur.rowcount > 0


def current_session_id(client: str, open_if_missing: bool = True) -> str:
    """Return the active session id for a client. Opens one if none exists."""
    p = _pointer_path(client.strip().lower() or "unknown")
    if p.exists():
        sid = p.read_text(encoding="utf-8").strip()
        if sid:
            return sid
    if open_if_missing:
        return open_session(client)
    raise RuntimeError(f"No active session for client={client!r}")


def log_access(session_id: str, fragment_id: str) -> None:
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO session_accesses (session_id, fragment_id, accessed_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id, fragment_id) DO UPDATE SET accessed_at = excluded.accessed_at
            """,
            (session_id, fragment_id, now),
        )


def auto_close_stale(hours: int | None = None) -> int:
    """Close any sessions older than `hours` that are still open. Returns count."""
    hrs = hours if hours is not None else config.SESSION_STALE_HOURS
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hrs)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE ended_at IS NULL AND started_at < ?",
            (_now(), cutoff),
        )
        return cur.rowcount


def last_n_session_ids(n: int = 2) -> list[str]:
    """Return the most recent N session ids across all clients, newest first."""
    with get_ro_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM sessions ORDER BY started_at DESC LIMIT ?",
            (n,),
        ).fetchall()
    return [r["id"] for r in rows]


def accessed_fragment_ids_in_sessions(session_ids: list[str]) -> set[str]:
    if not session_ids:
        return set()
    placeholders = ",".join("?" * len(session_ids))
    with get_ro_conn() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT fragment_id FROM session_accesses WHERE session_id IN ({placeholders})",
            session_ids,
        ).fetchall()
    return {r["fragment_id"] for r in rows}


def idle_sessions(idle_minutes: int) -> list[tuple[str, str]]:
    """Return (session_id, client) for open sessions with no ledger activity in `idle_minutes`.

    Uses the most recent timestamp across session_accesses AND session_ledger
    so either "read" or "write" activity keeps the session alive.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    with get_ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.client
            FROM sessions s
            WHERE s.ended_at IS NULL
              AND s.started_at < ?
              AND COALESCE((
                  SELECT MAX(ts) FROM (
                      SELECT MAX(accessed_at) AS ts FROM session_accesses WHERE session_id = s.id
                      UNION ALL
                      SELECT MAX(created_at)  AS ts FROM session_ledger    WHERE session_id = s.id
                  )
              ), s.started_at) < ?
            """,
            (cutoff, cutoff),
        ).fetchall()
    return [(r["id"], r["client"]) for r in rows]
