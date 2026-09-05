"""Session + access-log bookkeeping.

Each AI client opens its own session on start-up (or implicitly on first
recall/remember). Access events are logged per session so the decay loop can
consult "was this fragment touched in the current or previous session?".
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ulid import ULID

from hippocampus import config
from hippocampus.storage.db import get_conn, get_ro_conn

_TTY_CACHE: tuple[tuple[str | None, ...], str | None] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _clean_client(client: str) -> str:
    return client.strip().lower() or "unknown"


def _safe_part(value: str, *, fallback: str = "default", limit: int = 80) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    cleaned = cleaned.strip(".-")
    return (cleaned or fallback)[:limit]


def _detect_tty() -> str | None:
    """Best-effort TTY detection even when stdin/stdout are pipes."""
    global _TTY_CACHE
    cache_key = (
        os.environ.get("HIPPOCAMPUS_TTY"),
        os.environ.get("TTY"),
        os.environ.get("SSH_TTY"),
        os.environ.get("TERM_SESSION_ID"),
        os.environ.get("WEZTERM_PANE"),
        os.environ.get("TMUX_PANE"),
        os.environ.get("STY"),
        str(os.getpid()),
    )
    if _TTY_CACHE and _TTY_CACHE[0] == cache_key:
        return _TTY_CACHE[1]

    detected: str | None = None
    for key in ("HIPPOCAMPUS_TTY", "TTY", "SSH_TTY"):
        raw = os.environ.get(key)
        if raw and raw.strip() and raw.strip().lower() != "not a tty":
            detected = raw.strip()
            _TTY_CACHE = (cache_key, detected)
            return detected

    for fd in (0, 1, 2):
        try:
            if os.isatty(fd):
                detected = os.ttyname(fd)
                _TTY_CACHE = (cache_key, detected)
                return detected
        except OSError:
            pass

    # MCP/hook children often have stdio pipes. Walk parents until we find
    # the user's terminal TTY.
    pid = os.getpid()
    for _ in range(8):
        try:
            out = subprocess.run(
                ["ps", "-o", "ppid=", "-o", "tty=", "-p", str(pid)],
                check=False,
                capture_output=True,
                text=True,
                timeout=0.2,
            ).stdout.strip()
        except Exception:
            break
        if not out:
            break
        parts = out.split(None, 1)
        if not parts:
            break
        try:
            parent = int(parts[0])
        except ValueError:
            break
        tty = parts[1].strip() if len(parts) > 1 else ""
        if tty and tty not in {"?", "??"}:
            detected = tty
            _TTY_CACHE = (cache_key, detected)
            return detected
        if parent <= 1 or parent == pid:
            break
        pid = parent
    _TTY_CACHE = (cache_key, detected)
    return detected


def _detect_cwd() -> str | None:
    raw = os.environ.get("HIPPOCAMPUS_CWD") or os.environ.get("PWD")
    try:
        return str(Path(raw or os.getcwd()).resolve())
    except OSError:
        return raw or None


def _detect_workspace() -> str | None:
    """Best-effort workspace-root detection for GUI editors (Cursor, VS Code).

    GUI editors spawn the MCP server and the lifecycle hooks WITHOUT a shared
    controlling TTY and from an arbitrary, inconsistent cwd (``/``, ``$HOME``,
    the workspace, ...). They do, however, surface the workspace root: Cursor
    and VS Code set ``WORKSPACE_FOLDER_PATHS`` in the MCP server's environment,
    and the Cursor hooks export the same value (derived from the
    ``workspace_roots`` they receive on stdin). Keying the session off the
    workspace instead of the flaky cwd is what keeps the MCP-tool session and
    the hook-driven session unified for these clients.

    Returns the first (resolved) workspace root, or ``None`` when no workspace
    signal is present (e.g. terminal clients, which fall back to TTY + cwd).
    """
    raw = (
        os.environ.get("HIPPOCAMPUS_WORKSPACE")
        or os.environ.get("WORKSPACE_FOLDER_PATHS")
        or ""
    ).strip()
    if not raw:
        return None
    # Multi-root workspaces join paths with os.pathsep (":" POSIX / ";" Win).
    # POSIX paths contain no ":", so this split is safe; take the first root so
    # the MCP server and the hooks agree on a single stable key.
    first = raw.split(os.pathsep)[0].strip()
    if not first:
        return None
    try:
        return str(Path(first).resolve())
    except OSError:
        return first


def derive_session_key(session_key: str | None = None) -> str:
    """Return the stable context key for this client process.

    Precedence:
      1. explicit argument / HIPPOCAMPUS_SESSION_KEY
      2. terminal TTY (or terminal-session env) + cwd   — terminal clients
      3. workspace root (WORKSPACE_FOLDER_PATHS)         — GUI editors w/o a TTY
      4. cwd-only
      5. "default" for non-terminal contexts with no signal

    GUI editors (Cursor, VS Code) are keyed off the workspace root rather than
    cwd: they launch the MCP server and the hooks from different, unstable cwds,
    so the workspace is the only signal both sides share. Without this, the AI's
    own MCP tool calls (log_progress / get_progress / end_progress) land in a
    different session than the hook-injected snapshot it reads, splitting working
    memory. Terminal clients keep their tty+cwd key unchanged.
    """
    explicit = (session_key or os.environ.get("HIPPOCAMPUS_SESSION_KEY") or "").strip()
    if explicit:
        return _safe_part(explicit)

    tty = _detect_tty()
    cwd = _detect_cwd()
    term_hint = (
        os.environ.get("TERM_SESSION_ID")
        or os.environ.get("WEZTERM_PANE")
        or os.environ.get("TMUX_PANE")
        or os.environ.get("STY")
    )
    # Workspace only substitutes for cwd in a pure GUI context (no shared TTY or
    # terminal-session signal). Terminal clients keep their existing tty+cwd key
    # untouched so their already-working sessions don't churn.
    workspace = None if (tty or term_hint) else _detect_workspace()

    parts: list[str] = []
    if tty:
        parts.append(f"tty={tty}")
    elif term_hint:
        parts.append(f"term={term_hint}")
    if workspace:
        parts.append(f"ws={workspace}")
    elif cwd:
        parts.append(f"cwd={cwd}")
    if not parts:
        return "default"

    raw = "|".join(parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    labels: list[str] = []
    if tty:
        labels.append("tty-" + _safe_part(Path(tty).name or tty, fallback="tty", limit=24))
    elif term_hint:
        labels.append("term-" + _safe_part(term_hint, fallback="term", limit=24))
    if workspace:
        labels.append("ws-" + _safe_part(Path(workspace).name or "ws", fallback="ws", limit=32))
    elif cwd:
        labels.append("cwd-" + _safe_part(Path(cwd).name or "cwd", fallback="cwd", limit=32))
    prefix = "-".join(labels) or "ctx"
    return _safe_part(f"{prefix}-{digest}", limit=120)


def _pointer_path(client: str, session_key: str):
    return config.SESSION_POINTER_DIR / _safe_part(client) / f"{_safe_part(session_key)}.id"


def _legacy_pointer_path(client: str):
    return config.SESSION_POINTER_DIR / f"{client}.id"


def _active_session_exists(session_id: str, client: str, session_key: str) -> bool:
    with get_ro_conn() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM sessions
            WHERE id = ? AND client = ? AND session_key = ? AND ended_at IS NULL
            LIMIT 1
            """,
            (session_id, client, session_key),
        ).fetchone()
    return row is not None


def _latest_open_session(client: str, session_key: str) -> str | None:
    with get_ro_conn() as conn:
        row = conn.execute(
            """
            SELECT id FROM sessions
            WHERE client = ? AND session_key = ? AND ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (client, session_key),
        ).fetchone()
    return row["id"] if row else None


def open_session(client: str, session_key: str | None = None) -> str:
    """Open or reuse the active session for a client/context."""
    client = _clean_client(client)
    key = derive_session_key(session_key)
    config.ensure_dirs()
    sid = f"sess_{ULID()}"
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, client, session_key, started_at) VALUES (?, ?, ?, ?)",
                (sid, client, key, _now()),
            )
    except sqlite3.IntegrityError:
        active = _latest_open_session(client, key)
        if active is None:
            raise
        sid = active
    pointer = _pointer_path(client, key)
    pointer.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    pointer.parent.chmod(0o700)
    pointer.write_text(sid, encoding="utf-8")
    pointer.chmod(0o600)
    return sid


def ensure_session(client: str, session_key: str | None = None) -> str:
    """Return the active session for client/context, opening one if needed."""
    return current_session_id(client, session_key=session_key, open_if_missing=True)


def rotate(client: str, session_key: str | None = None) -> str:
    """Close the current session for `client`/context and open a fresh one.

    Used by `end_progress` and whenever the AI client starts a new task. The
    previous session's ledger is preserved (the rows are kept) but stops
    appearing in the rendered WORKING block.
    """
    client_name = _clean_client(client)
    key = derive_session_key(session_key)
    try:
        current = current_session_id(client_name, session_key=key, open_if_missing=False)
        close_session(current)
    except RuntimeError:
        pass
    return open_session(client_name, session_key=key)


def close_session(session_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (_now(), session_id),
        )
        return cur.rowcount > 0


def current_session_id(
    client: str,
    session_key: str | None = None,
    open_if_missing: bool = True,
) -> str:
    """Return the active session id for a client/context. Opens one if needed."""
    client_name = _clean_client(client)
    key = derive_session_key(session_key)
    p = _pointer_path(client_name, key)
    if p.exists():
        sid = p.read_text(encoding="utf-8").strip()
        if sid and _active_session_exists(sid, client_name, key):
            return sid
    elif key == "default":
        legacy = _legacy_pointer_path(client_name)
        if legacy.exists():
            sid = legacy.read_text(encoding="utf-8").strip()
            if sid and _active_session_exists(sid, client_name, key):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(sid, encoding="utf-8")
                p.chmod(0o600)
                return sid

    latest = _latest_open_session(client_name, key)
    if latest:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(latest, encoding="utf-8")
        p.chmod(0o600)
        return latest

    if open_if_missing:
        return open_session(client_name, session_key=key)
    raise RuntimeError(f"No active session for client={client_name!r} session_key={key!r}")


def log_access(session_id: str, fragment_id: str, via: str = "recall") -> None:
    """Record an access. `via='inject'` never overwrites a real recall."""
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO session_accesses (session_id, fragment_id, accessed_at, via)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id, fragment_id) DO UPDATE SET
                accessed_at = excluded.accessed_at,
                via = CASE WHEN session_accesses.via = 'recall' THEN 'recall' ELSE excluded.via END
            """,
            (session_id, fragment_id, now, via),
        )


def injected_fragment_ids(session_id: str) -> set[str]:
    with get_ro_conn() as conn:
        rows = conn.execute(
            "SELECT fragment_id FROM session_accesses WHERE session_id = ? AND via = 'inject'",
            (session_id,),
        ).fetchall()
    return {r["fragment_id"] for r in rows}


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


def idle_sessions(idle_minutes: int) -> list[tuple[str, str, str]]:
    """Return (session_id, client, session_key) for idle open sessions.

    Uses the most recent timestamp across session_accesses AND session_ledger
    so either "read" or "write" activity keeps the session alive.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    with get_ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.client, s.session_key
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
    return [(r["id"], r["client"], r["session_key"]) for r in rows]
