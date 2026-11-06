"""A2A message store (hub mailbox + local inbox) in its own sqlite file.

Never touches the sync oplog or the fragment DB. All safety limits live here so
every transport (hub HTTP, local same-device) is bound by them.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from ulid import ULID

from hippocampus import config

MAX_CONTENT_BYTES = 16 * 1024
PRESENCE_SECONDS = 600
LEASE_SECONDS = 60  # a device-level message claimed by one session but not acked is re-offered after this
ADDRESS_RE = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+(/[A-Za-z0-9_.=-]+)?$")
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")  # msg/thread ids are shown to models: keep them inert

_SCHEMA = """
CREATE TABLE IF NOT EXISTS a2a_messages (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id       TEXT NOT NULL UNIQUE,
    thread_id    TEXT NOT NULL,
    in_reply_to  TEXT,
    sender       TEXT NOT NULL,
    recipient    TEXT NOT NULL,
    content      TEXT NOT NULL,
    hop          INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    delivered_at TEXT,
    delivered_to TEXT
);
CREATE INDEX IF NOT EXISTS idx_a2a_thread ON a2a_messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_a2a_sender ON a2a_messages(sender, created_at);
CREATE TABLE IF NOT EXISTS a2a_audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    event   TEXT NOT NULL,
    msg_id  TEXT,
    sender  TEXT,
    recipient TEXT,
    thread_id TEXT,
    hop     INTEGER,
    reason  TEXT
);
CREATE TABLE IF NOT EXISTS a2a_presence (address TEXT PRIMARY KEY, last_seen TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS a2a_state (key TEXT PRIMARY KEY, value TEXT);
-- local side: messages received by this device's MCP poller
CREATE TABLE IF NOT EXISTS a2a_inbox (
    msg_id      TEXT PRIMARY KEY,
    address     TEXT NOT NULL,
    sender      TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    in_reply_to TEXT,
    hop         INTEGER NOT NULL,
    content     TEXT NOT NULL,
    received_at TEXT NOT NULL,
    shown_at    TEXT
);
CREATE TABLE IF NOT EXISTS a2a_peer_turn (
    session_key TEXT PRIMARY KEY,
    msg_id      TEXT NOT NULL,
    sender      TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    hop         INTEGER NOT NULL,
    started_at  TEXT NOT NULL
);
-- remember() calls made during a peer turn; never synced (separate DB file), approved via CLI
CREATE TABLE IF NOT EXISTS quarantined_fragments (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    session_key TEXT NOT NULL,
    client      TEXT,
    msg_id      TEXT,
    sender      TEXT,
    args        TEXT NOT NULL
);
"""


class A2AError(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def db_path(near: Path | None = None) -> Path:
    env = os.environ.get("HIPPOCAMPUS_A2A_DB")
    if env:
        return Path(env)
    return (near.parent if near else config.HIPPOCAMPUS_HOME) / "a2a.db"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive columns for a2a.db files created before them."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(a2a_messages)")}
    for col in ("claimed_by", "claimed_at"):
        if col not in have:
            try:
                conn.execute(f"ALTER TABLE a2a_messages ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError as exc:  # another process added it first
                if "duplicate column" not in str(exc):
                    raise


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.execute("BEGIN IMMEDIATE")  # serialises the limit checks with the insert
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    config.secure_file(path)


def base_address(address: str) -> str:
    return address.split("/", 1)[0]


def _audit(conn, event: str, *, msg_id=None, sender=None, recipient=None, thread_id=None, hop=None, reason=None) -> None:
    conn.execute(
        "INSERT INTO a2a_audit(ts, event, msg_id, sender, recipient, thread_id, hop, reason) VALUES (?,?,?,?,?,?,?,?)",
        (_now(), event, msg_id, sender, recipient, thread_id, hop, reason),
    )


def _is_paused(conn) -> bool:
    row = conn.execute("SELECT value FROM a2a_state WHERE key = 'paused'").fetchone()
    return bool(row and row["value"] == "1")


def set_paused(path: Path, paused: bool) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO a2a_state(key, value) VALUES ('paused', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("1" if paused else "0",),
        )
        _audit(conn, "pause" if paused else "resume")


def _msg_dict(row) -> dict[str, Any]:
    return {
        "seq": row["seq"], "msg_id": row["msg_id"], "from": row["sender"], "to": row["recipient"],
        "thread_id": row["thread_id"], "in_reply_to": row["in_reply_to"], "hop": row["hop"],
        "content": row["content"], "created_at": row["created_at"],
    }


def _send(conn, sender, to, content, msg_id, thread_id, in_reply_to) -> dict[str, Any]:
    for addr in (sender, to):
        if not ADDRESS_RE.match(addr or ""):
            raise A2AError(400, f"bad address: {addr!r}")
    if not msg_id or not content:
        raise A2AError(400, "msg_id and content required")
    for name, value in (("msg_id", msg_id), ("thread_id", thread_id), ("in_reply_to", in_reply_to)):
        if value is not None and not ID_RE.match(value):
            raise A2AError(400, f"bad {name}")
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise A2AError(413, f"content over {MAX_CONTENT_BYTES} bytes")
    if _is_paused(conn):
        raise A2AError(423, "a2a paused")
    existing = conn.execute("SELECT * FROM a2a_messages WHERE msg_id = ?", (msg_id,)).fetchone()
    if existing:  # idempotent retry: no second delivery
        return {"msg_id": msg_id, "thread_id": existing["thread_id"], "hop": existing["hop"]}

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    recent = conn.execute(
        "SELECT COUNT(*) AS n FROM a2a_messages WHERE (sender = ? OR sender LIKE ?) AND created_at > ?",
        (base_address(sender), base_address(sender) + "/%", cutoff),
    ).fetchone()["n"]
    if recent >= int(config.get_setting("a2a_rate_per_min") or 30):
        raise A2AError(429, "rate limit")

    # hop is computed here; the client's value is never accepted.
    if in_reply_to:
        parent = conn.execute("SELECT thread_id, hop FROM a2a_messages WHERE msg_id = ?", (in_reply_to,)).fetchone()
        if not parent:
            raise A2AError(404, "in_reply_to not found")
        thread, hop = parent["thread_id"], parent["hop"] + 1
    else:
        thread = thread_id or f"thr_{ULID()}"
        top = conn.execute("SELECT MAX(hop) AS h FROM a2a_messages WHERE thread_id = ?", (thread,)).fetchone()["h"]
        hop = 0 if top is None else top + 1  # reusing a thread id cannot reset the hop count
    if hop > int(config.get_setting("a2a_max_hops") or 3):
        raise A2AError(409, "max hops exceeded")
    n = conn.execute("SELECT COUNT(*) AS n FROM a2a_messages WHERE thread_id = ?", (thread,)).fetchone()["n"]
    if n >= int(config.get_setting("a2a_max_thread_msgs") or 20):
        raise A2AError(409, "thread message cap reached")

    conn.execute(
        "INSERT INTO a2a_messages(msg_id, thread_id, in_reply_to, sender, recipient, content, hop, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (msg_id, thread, in_reply_to, sender, to, content, hop, _now()),
    )
    _audit(conn, "send", msg_id=msg_id, sender=sender, recipient=to, thread_id=thread, hop=hop)
    return {"msg_id": msg_id, "thread_id": thread, "hop": hop}


def send(path: Path, *, sender: str, to: str, content: str, msg_id: str,
         thread_id: str | None = None, in_reply_to: str | None = None) -> dict[str, Any]:
    try:
        with connect(path) as conn:
            return _send(conn, sender, to, content, msg_id, thread_id, in_reply_to)
    except A2AError as exc:
        with connect(path) as conn:  # the failed txn rolled back; audit the rejection separately
            _audit(conn, "reject", msg_id=msg_id, sender=sender, recipient=to, thread_id=thread_id, reason=f"{exc.status} {exc.reason}")
        raise


def _fetch(conn, address: str, since: int) -> list[dict[str, Any]]:
    # A device-level (base) address is leased to one polling session at a time; session-qualified rows are exclusive anyway.
    base = base_address(address)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=LEASE_SECONDS)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    rows = conn.execute(
        "SELECT * FROM a2a_messages WHERE seq > ? AND delivered_at IS NULL AND ((recipient = ? AND instr(recipient, '/') > 0) OR (recipient = ? AND "
        "(claimed_by IS NULL OR claimed_by = ? OR claimed_at < ?))) ORDER BY seq LIMIT 100",
        (since, address, base, address, cutoff),
    ).fetchall()
    claim = [r["msg_id"] for r in rows if r["recipient"] == base]
    if claim:  # same BEGIN IMMEDIATE txn as the select: a concurrent poller cannot claim the same rows
        now = _now()
        conn.executemany("UPDATE a2a_messages SET claimed_by = ?, claimed_at = ? WHERE msg_id = ?", [(address, now, m) for m in claim])
    return [_msg_dict(r) for r in rows]


def inbox(path: Path, address: str, since: int = 0, wait: int = 0) -> dict[str, Any]:
    """Undelivered messages for `address`; long-polls up to `wait` seconds."""
    if not ADDRESS_RE.match(address or ""):
        raise A2AError(400, f"bad address: {address!r}")
    deadline = time.monotonic() + max(0, min(int(wait), 25))
    first = True
    while True:
        with connect(path) as conn:
            if first:
                conn.execute(
                    "INSERT INTO a2a_presence(address, last_seen) VALUES (?, ?) ON CONFLICT(address) DO UPDATE SET last_seen = excluded.last_seen",
                    (address, _now()),
                )
                first = False
            msgs = [] if _is_paused(conn) else _fetch(conn, address, since)
            for m in msgs:
                _audit(conn, "deliver", msg_id=m["msg_id"], sender=m["from"], recipient=address, thread_id=m["thread_id"], hop=m["hop"])
        if msgs or time.monotonic() >= deadline:
            return {"messages": msgs, "cursor": msgs[-1]["seq"] if msgs else since}
        time.sleep(0.5)  # shortcut: sqlite polling, fine for a handful of agents


def ack(path: Path, address: str, msg_ids: list[str]) -> int:
    n = 0
    with connect(path) as conn:
        for mid in msg_ids:
            cur = conn.execute(
                "UPDATE a2a_messages SET delivered_at = ?, delivered_to = ? WHERE msg_id = ? AND delivered_at IS NULL AND (recipient = ? OR recipient = ?)",
                (_now(), address, mid, address, base_address(address)),
            )
            if cur.rowcount:  # first ack wins
                n += 1
                _audit(conn, "ack", msg_id=mid, recipient=address)
    return n


def agents(path: Path) -> list[dict[str, str]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=PRESENCE_SECONDS)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with connect(path) as conn:
        rows = conn.execute("SELECT address, last_seen FROM a2a_presence WHERE last_seen > ? ORDER BY address", (cutoff,)).fetchall()
    return [{"address": r["address"], "last_seen": r["last_seen"]} for r in rows]


def audit(path: Path, limit: int = 100) -> list[dict[str, Any]]:
    with connect(path) as conn:
        rows = conn.execute("SELECT * FROM a2a_audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),)).fetchall()
    return [dict(r) for r in rows]


# --- local inbox (written by the MCP poller, read by hooks and tools) -------

def store_received(path: Path, address: str, msgs: list[dict[str, Any]]) -> None:
    with connect(path) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO a2a_inbox(msg_id, address, sender, thread_id, in_reply_to, hop, content, received_at) VALUES (?,?,?,?,?,?,?,?)",
            [(m["msg_id"], address, m["from"], m["thread_id"], m.get("in_reply_to"), m["hop"], m["content"], _now()) for m in msgs],
        )


def local_unread(path: Path, address: str, limit: int = 5, *, mark: bool = True) -> list[dict[str, Any]]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM a2a_inbox WHERE address = ? AND shown_at IS NULL ORDER BY received_at LIMIT ?", (address, limit)
        ).fetchall()
        if mark and rows:
            conn.executemany("UPDATE a2a_inbox SET shown_at = ? WHERE msg_id = ?", [(_now(), r["msg_id"]) for r in rows])
    return [dict(r) for r in rows]


def local_mark_shown(path: Path, msg_id: str) -> None:
    with connect(path) as conn:
        conn.execute("UPDATE a2a_inbox SET shown_at = COALESCE(shown_at, ?) WHERE msg_id = ?", (_now(), msg_id))


def local_recent(path: Path, address: str, limit: int = 20) -> list[dict[str, Any]]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM a2a_inbox WHERE address = ? ORDER BY received_at DESC LIMIT ?", (address, limit)
        ).fetchall()
        conn.execute("UPDATE a2a_inbox SET shown_at = COALESCE(shown_at, ?) WHERE address = ?", (_now(), address))
    return [dict(r) for r in rows]


def local_get(path: Path, address: str, msg_id: str) -> dict[str, Any] | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM a2a_inbox WHERE address = ? AND msg_id = ?", (address, msg_id)).fetchone()
    return dict(row) if row else None


def local_latest_from(path: Path, address: str, sender: str) -> dict[str, Any] | None:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM a2a_inbox WHERE address = ? AND sender = ? ORDER BY received_at DESC LIMIT 1", (address, sender)
        ).fetchone()
    return dict(row) if row else None


# --- memory quarantine -------------------------------------------------------

def quarantine_add(path: Path, *, session_key: str, client: str, turn: dict[str, Any], args: dict[str, Any]) -> str:
    qid = f"q_{ULID()}"
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO quarantined_fragments(id, created_at, session_key, client, msg_id, sender, args) VALUES (?,?,?,?,?,?,?)",
            (qid, _now(), session_key, client, turn.get("msg_id"), turn.get("sender"), json.dumps(args)),
        )
        _audit(conn, "quarantine", msg_id=turn.get("msg_id"), sender=turn.get("sender"), recipient=session_key, reason=qid)
    return qid


def quarantine_list(path: Path) -> list[dict[str, Any]]:
    with connect(path) as conn:
        rows = conn.execute("SELECT * FROM quarantined_fragments ORDER BY created_at").fetchall()
    return [{**dict(r), "args": json.loads(r["args"])} for r in rows]


def quarantine_get(path: Path, qid: str) -> dict[str, Any] | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM quarantined_fragments WHERE id = ?", (qid,)).fetchone()
    return {**dict(row), "args": json.loads(row["args"])} if row else None


def quarantine_resolve(path: Path, qid: str, event: str, reason: str | None = None) -> bool:
    """Delete a quarantine row and audit `event` (quarantine_approve / quarantine_reject)."""
    with connect(path) as conn:
        row = conn.execute("SELECT msg_id, sender FROM quarantined_fragments WHERE id = ?", (qid,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM quarantined_fragments WHERE id = ?", (qid,))
        _audit(conn, event, msg_id=row["msg_id"], sender=row["sender"], reason=f"{qid} {reason or ''}".strip())
    return True
