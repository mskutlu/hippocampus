"""Raw/visible transcript storage for sessions.

This layer intentionally stays separate from `fragments`: transcript rows can
contain raw prompts and visible assistant responses, while fragments should
remain synthesized long-term memory.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from hippocampus import config
from hippocampus.storage.db import get_conn, get_ro_conn

VALID_ROLES: frozenset[str] = frozenset(
    {"user", "assistant", "assistant_summary", "reasoning_summary", "system", "tool"}
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class TranscriptEntry:
    id: int
    session_id: str
    client: str
    session_key: str
    role: str
    content: str
    source_event: str | None
    metadata: dict[str, Any] | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "client": self.client,
            "session_key": self.session_key,
            "role": self.role,
            "content": self.content,
            "source_event": self.source_event,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


def _row_to_entry(row: sqlite3.Row) -> TranscriptEntry:
    metadata = None
    raw_meta = row["metadata_json"]
    if raw_meta:
        try:
            metadata = json.loads(raw_meta)
        except json.JSONDecodeError:
            metadata = {"raw": raw_meta}
    return TranscriptEntry(
        id=int(row["id"]),
        session_id=row["session_id"],
        client=row["client"],
        session_key=row["session_key"],
        role=row["role"],
        content=row["content"],
        source_event=row["source_event"],
        metadata=metadata,
        created_at=row["created_at"],
    )


def _recent_duplicate(
    conn: sqlite3.Connection,
    session_id: str,
    role: str,
    content: str,
    *,
    window_seconds: int = 60,
) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    row = conn.execute(
        """
        SELECT 1 FROM session_transcript
        WHERE session_id = ?
          AND role = ?
          AND content = ?
          AND created_at >= ?
        LIMIT 1
        """,
        (session_id, role, content, cutoff),
    ).fetchone()
    return row is not None


def log_entry(
    *,
    session_id: str,
    client: str,
    session_key: str,
    role: str,
    content: str,
    source_event: str | None = None,
    metadata: dict[str, Any] | None = None,
    dedup_seconds: int = 60,
) -> TranscriptEntry | None:
    if role not in VALID_ROLES:
        raise ValueError(f"invalid transcript role {role!r}; expected one of {sorted(VALID_ROLES)}")
    cleaned = (content or "").strip()
    if not cleaned:
        raise ValueError("content must be non-empty")

    metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True) if metadata else None
    with get_conn() as conn:
        retention_days = int(config.get_setting("transcript_retention_days") or 0)
        if retention_days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            conn.execute("DELETE FROM session_transcript WHERE created_at < ?", (cutoff,))
        if dedup_seconds > 0 and _recent_duplicate(
            conn, session_id, role, cleaned, window_seconds=dedup_seconds
        ):
            return None
        cur = conn.execute(
            """
            INSERT INTO session_transcript
                (session_id, client, session_key, role, content, source_event, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, client, session_key, role, cleaned, source_event, metadata_json, _now()),
        )
        row = conn.execute(
            "SELECT * FROM session_transcript WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return _row_to_entry(row)


def current_entries(session_id: str, limit: int = 200) -> list[TranscriptEntry]:
    with get_ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM session_transcript
            WHERE session_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    return [_row_to_entry(r) for r in rows]


def entries_by_client(client: str, session_key: str | None = None, limit: int = 200) -> list[TranscriptEntry]:
    query = "SELECT * FROM session_transcript WHERE client = ?"
    params: list[Any] = [client]
    if session_key is not None:
        query += " AND session_key = ?"
        params.append(session_key)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_ro_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_entry(r) for r in rows]


def all_entries(client: str | None = None) -> list[TranscriptEntry]:
    query = "SELECT * FROM session_transcript"
    params: list[Any] = []
    if client:
        query += " WHERE client = ?"
        params.append(client)
    query += " ORDER BY created_at ASC"
    with get_ro_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_entry(row) for row in rows]


def purge(*, client: str | None = None, older_than_days: int | None = None) -> int:
    clauses: list[str] = []
    params: list[Any] = []
    if client:
        clauses.append("client = ?")
        params.append(client)
    if older_than_days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        clauses.append("created_at < ?")
        params.append(cutoff)
    query = "DELETE FROM session_transcript"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    with get_conn() as conn:
        cursor = conn.execute(query, params)
    return cursor.rowcount
