"""Fragment CRUD + tag management + full-text search.

Every mutation fires the mirror sync hook so the Obsidian mirror stays
consistent with SQLite. The hook is injected (not forced on callers) so unit
tests can run without the vault directory.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Sequence

from ulid import ULID

from hippocampus import config
from hippocampus.storage.db import get_conn, get_ro_conn

# Callback signature: fn(fragment_dict) -> None. Set by the sync module.
_mirror_write_hook: Callable[[dict], None] | None = None
_mirror_delete_hook: Callable[[str], None] | None = None
_mirror_archive_hook: Callable[[str], None] | None = None


def register_mirror_hooks(
    *,
    write: Callable[[dict], None] | None = None,
    delete: Callable[[str], None] | None = None,
    archive: Callable[[str], None] | None = None,
) -> None:
    """Install mirror sync callbacks. Call once at process start."""
    global _mirror_write_hook, _mirror_delete_hook, _mirror_archive_hook
    if write is not None:
        _mirror_write_hook = write
    if delete is not None:
        _mirror_delete_hook = delete
    if archive is not None:
        _mirror_archive_hook = archive


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class Fragment:
    id: str
    content: str
    summary: str = ""
    source_type: str = "manual"
    source_ref: str | None = None
    confidence: float = config.CONFIDENCE_INIT
    accessed: int = 0
    last_accessed_at: str | None = None
    created_at: str = ""
    updated_at: str = ""
    pinned: bool = False
    below_threshold_since: str | None = None
    tags: list[str] = field(default_factory=list)
    associated_with: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "summary": self.summary,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "confidence": round(self.confidence, 6),
            "accessed": self.accessed,
            "last_accessed_at": self.last_accessed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "pinned": self.pinned,
            "below_threshold_since": self.below_threshold_since,
            "tags": list(self.tags),
            "associated_with": list(self.associated_with),
        }


def _row_to_fragment(row: sqlite3.Row, tags: list[str], assoc: list[str]) -> Fragment:
    return Fragment(
        id=row["id"],
        content=row["content"],
        summary=row["summary"] or "",
        source_type=row["source_type"],
        source_ref=row["source_ref"],
        confidence=float(row["confidence"]),
        accessed=int(row["accessed"]),
        last_accessed_at=row["last_accessed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        pinned=bool(row["pinned"]),
        below_threshold_since=row["below_threshold_since"],
        tags=tags,
        associated_with=assoc,
    )


def _fetch_tags(conn: sqlite3.Connection, fragment_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT tag FROM fragment_tags WHERE fragment_id = ? ORDER BY tag",
        (fragment_id,),
    ).fetchall()
    return [r["tag"] for r in rows]


def _fetch_associations(conn: sqlite3.Connection, fragment_id: str, limit: int = 20) -> list[str]:
    rows = conn.execute(
        """
        SELECT CASE WHEN fragment_a = ? THEN fragment_b ELSE fragment_a END AS other
        FROM associations
        WHERE fragment_a = ? OR fragment_b = ?
        ORDER BY weight DESC
        LIMIT ?
        """,
        (fragment_id, fragment_id, fragment_id, limit),
    ).fetchall()
    return [r["other"] for r in rows]


def create(
    content: str,
    summary: str = "",
    tags: Sequence[str] = (),
    source_type: str = "manual",
    source_ref: str | None = None,
    pinned: bool = False,
) -> Fragment:
    """Insert a new fragment. Returns the hydrated Fragment."""
    fid = f"frag_{ULID()}"
    now = _utc_now()

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO fragments
                (id, content, summary, source_type, source_ref,
                 confidence, accessed, created_at, updated_at, pinned)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (fid, content, summary, source_type, source_ref,
             config.CONFIDENCE_INIT, now, now, 1 if pinned else 0),
        )
        if tags:
            conn.executemany(
                "INSERT OR IGNORE INTO fragment_tags(fragment_id, tag) VALUES (?, ?)",
                [(fid, t.strip()) for t in tags if t.strip()],
            )
        row = conn.execute("SELECT * FROM fragments WHERE id = ?", (fid,)).fetchone()
        frag_tags = _fetch_tags(conn, fid)

    fragment = _row_to_fragment(row, frag_tags, [])
    if _mirror_write_hook:
        _mirror_write_hook(fragment.to_dict())
    return fragment


def get(fragment_id: str) -> Fragment | None:
    with get_ro_conn() as conn:
        row = conn.execute("SELECT * FROM fragments WHERE id = ?", (fragment_id,)).fetchone()
        if not row:
            return None
        tags = _fetch_tags(conn, fragment_id)
        assoc = _fetch_associations(conn, fragment_id)
    return _row_to_fragment(row, tags, assoc)


def update_fields(
    fragment_id: str,
    *,
    content: str | None = None,
    summary: str | None = None,
    confidence: float | None = None,
    accessed_delta: int = 0,
    last_accessed_at: str | None = None,
    pinned: bool | None = None,
    below_threshold_since: str | None | bool = False,  # False = don't touch, None = clear, str = set
    add_tags: Sequence[str] = (),
    remove_tags: Sequence[str] = (),
) -> Fragment | None:
    """Partial update. Mirror is refreshed after the transaction commits."""
    sets: list[str] = []
    params: list = []

    if content is not None:
        sets.append("content = ?")
        params.append(content)
    if summary is not None:
        sets.append("summary = ?")
        params.append(summary)
    if confidence is not None:
        bounded = max(config.CONFIDENCE_MIN, min(config.CONFIDENCE_MAX, confidence))
        sets.append("confidence = ?")
        params.append(bounded)
    if accessed_delta:
        sets.append("accessed = accessed + ?")
        params.append(accessed_delta)
    if last_accessed_at is not None:
        sets.append("last_accessed_at = ?")
        params.append(last_accessed_at)
    if pinned is not None:
        sets.append("pinned = ?")
        params.append(1 if pinned else 0)
    if below_threshold_since is not False:  # explicit None or str
        sets.append("below_threshold_since = ?")
        params.append(below_threshold_since)

    sets.append("updated_at = ?")
    params.append(_utc_now())

    with get_conn() as conn:
        if sets:
            params.append(fragment_id)
            conn.execute(f"UPDATE fragments SET {', '.join(sets)} WHERE id = ?", params)
        if add_tags:
            conn.executemany(
                "INSERT OR IGNORE INTO fragment_tags(fragment_id, tag) VALUES (?, ?)",
                [(fragment_id, t.strip()) for t in add_tags if t.strip()],
            )
        if remove_tags:
            conn.executemany(
                "DELETE FROM fragment_tags WHERE fragment_id = ? AND tag = ?",
                [(fragment_id, t) for t in remove_tags],
            )
        row = conn.execute("SELECT * FROM fragments WHERE id = ?", (fragment_id,)).fetchone()
        if not row:
            return None
        tags = _fetch_tags(conn, fragment_id)
        assoc = _fetch_associations(conn, fragment_id)

    fragment = _row_to_fragment(row, tags, assoc)
    if _mirror_write_hook:
        _mirror_write_hook(fragment.to_dict())
    return fragment


def delete(fragment_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM fragments WHERE id = ?", (fragment_id,))
        deleted = cur.rowcount > 0
    if deleted and _mirror_delete_hook:
        _mirror_delete_hook(fragment_id)
    return deleted


def archive(fragment_id: str) -> bool:
    """Move a fragment's mirror file to archive dir and delete from DB."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM fragments WHERE id = ?", (fragment_id,))
        deleted = cur.rowcount > 0
    if deleted and _mirror_archive_hook:
        _mirror_archive_hook(fragment_id)
    return deleted


def search_fts(query: str, limit: int = 20, min_confidence: float = 0.0) -> list[Fragment]:
    """Full-text search. Returns ordered by FTS rank first, then confidence."""
    if not query.strip():
        return []
    with get_ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT f.*
            FROM fragments_fts
            JOIN fragments f ON f.rowid = fragments_fts.rowid
            WHERE fragments_fts MATCH ?
              AND f.confidence >= ?
            ORDER BY fragments_fts.rank, f.confidence DESC
            LIMIT ?
            """,
            (query, min_confidence, limit),
        ).fetchall()
        frags: list[Fragment] = []
        for row in rows:
            tags = _fetch_tags(conn, row["id"])
            assoc = _fetch_associations(conn, row["id"])
            frags.append(_row_to_fragment(row, tags, assoc))
    return frags


def list_by_tag(tag: str, limit: int = 50) -> list[Fragment]:
    with get_ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT f.*
            FROM fragments f
            JOIN fragment_tags t ON t.fragment_id = f.id
            WHERE t.tag = ?
            ORDER BY f.confidence DESC
            LIMIT ?
            """,
            (tag, limit),
        ).fetchall()
        frags: list[Fragment] = []
        for row in rows:
            tags = _fetch_tags(conn, row["id"])
            assoc = _fetch_associations(conn, row["id"])
            frags.append(_row_to_fragment(row, tags, assoc))
    return frags


def list_all(
    min_confidence: float = 0.0,
    limit: int = 200,
    include_pinned: bool = True,
) -> list[Fragment]:
    query = "SELECT * FROM fragments WHERE confidence >= ?"
    params: list = [min_confidence]
    if not include_pinned:
        query += " AND pinned = 0"
    query += " ORDER BY confidence DESC, last_accessed_at DESC LIMIT ?"
    params.append(limit)
    with get_ro_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        frags: list[Fragment] = []
        for row in rows:
            tags = _fetch_tags(conn, row["id"])
            frags.append(_row_to_fragment(row, tags, []))
    return frags


def count() -> int:
    with get_ro_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM fragments").fetchone()
    return int(row["n"])


def iter_all() -> Iterable[Fragment]:
    """Stream every fragment (used by decay/archive loops)."""
    with get_ro_conn() as conn:
        for row in conn.execute("SELECT * FROM fragments").fetchall():
            tags = _fetch_tags(conn, row["id"])
            yield _row_to_fragment(row, tags, [])
