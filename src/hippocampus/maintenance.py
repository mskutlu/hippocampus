"""Operational health and cleanup helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hippocampus import config
from hippocampus.storage.db import get_conn, get_ro_conn
from hippocampus.storage import fragments as frag_store
from hippocampus.sync import obsidian_mirror


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fragment_file_ids() -> set[str]:
    if not config.FRAGMENTS_DIR.exists():
        return set()
    return {p.stem for p in config.FRAGMENTS_DIR.glob("frag_*.md") if p.is_file()}


def _db_fragment_ids() -> set[str]:
    with get_ro_conn() as conn:
        rows = conn.execute("SELECT id FROM fragments").fetchall()
    return {r["id"] for r in rows}


def health_snapshot(*, include_duplicates: bool = False) -> dict[str, Any]:
    """Return high-signal operational health metrics."""
    db_ids = _db_fragment_ids()
    file_ids = _fragment_file_ids()

    with get_ro_conn() as conn:
        fragments = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(pinned) AS pinned,
              SUM(CASE WHEN confidence >= 1.0 THEN 1 ELSE 0 END) AS maxed,
              ROUND(AVG(confidence), 6) AS avg_confidence
            FROM fragments
            """
        ).fetchone()
        sessions = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN ended_at IS NULL THEN 1 ELSE 0 END) AS open,
              SUM(CASE WHEN l.session_id IS NULL AND a.session_id IS NULL THEN 1 ELSE 0 END) AS no_activity
            FROM sessions s
            LEFT JOIN (SELECT DISTINCT session_id FROM session_ledger) l ON l.session_id = s.id
            LEFT JOIN (SELECT DISTINCT session_id FROM session_accesses) a ON a.session_id = s.id
            """
        ).fetchone()
        tags = conn.execute(
            """
            SELECT
              COUNT(*) AS distinct_tags,
              SUM(CASE WHEN n = 1 THEN 1 ELSE 0 END) AS singleton_tags
            FROM (SELECT tag, COUNT(*) AS n FROM fragment_tags GROUP BY tag)
            """
        ).fetchone()
        missing_embeddings = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM fragments f
            LEFT JOIN fragment_embeddings e ON e.fragment_id = f.id
            WHERE e.fragment_id IS NULL
            """
        ).fetchone()["n"]

    duplicate_candidates: list[dict[str, Any]] = []
    if include_duplicates:
        try:
            from hippocampus.embeddings import dedup

            duplicate_candidates = [
                {"keeper": p.keeper, "loser": p.loser, "score": round(p.score, 4)}
                for p in dedup.find_duplicates(limit=20)
            ]
        except Exception:
            duplicate_candidates = []

    return {
        "fragments": {
            "total": int(fragments["total"] or 0),
            "pinned": int(fragments["pinned"] or 0),
            "max_confidence": int(fragments["maxed"] or 0),
            "average_confidence": fragments["avg_confidence"] or 0.0,
            "missing_embeddings": int(missing_embeddings or 0),
        },
        "sessions": {
            "total": int(sessions["total"] or 0),
            "open": int(sessions["open"] or 0),
            "no_activity": int(sessions["no_activity"] or 0),
        },
        "tags": {
            "distinct": int(tags["distinct_tags"] or 0),
            "singletons": int(tags["singleton_tags"] or 0),
        },
        "mirror": {
            "files": len(file_ids),
            "db_fragments": len(db_ids),
            "orphan_files": len(file_ids - db_ids),
            "missing_files": len(db_ids - file_ids),
        },
        "duplicates": {
            "checked": include_duplicates,
            "candidates": duplicate_candidates,
        },
    }


def cleanup_sessions(*, dry_run: bool = True) -> dict[str, Any]:
    """Close/delete no-activity sessions and close duplicate active contexts."""
    now = _now()
    with get_conn() as conn:
        empty_open = conn.execute(
            """
            SELECT s.id
            FROM sessions s
            LEFT JOIN (SELECT DISTINCT session_id FROM session_ledger) l ON l.session_id = s.id
            LEFT JOIN (SELECT DISTINCT session_id FROM session_accesses) a ON a.session_id = s.id
            WHERE s.ended_at IS NULL
              AND l.session_id IS NULL
              AND a.session_id IS NULL
            """
        ).fetchall()
        empty_ended = conn.execute(
            """
            SELECT s.id
            FROM sessions s
            LEFT JOIN (SELECT DISTINCT session_id FROM session_ledger) l ON l.session_id = s.id
            LEFT JOIN (SELECT DISTINCT session_id FROM session_accesses) a ON a.session_id = s.id
            WHERE s.ended_at IS NOT NULL
              AND l.session_id IS NULL
              AND a.session_id IS NULL
            """
        ).fetchall()
        duplicate_open = conn.execute(
            """
            SELECT id
            FROM (
                SELECT
                  id,
                  ROW_NUMBER() OVER (
                    PARTITION BY client, session_key
                    ORDER BY started_at DESC
                  ) AS rn
                FROM sessions
                WHERE ended_at IS NULL
            )
            WHERE rn > 1
            """
        ).fetchall()

        if not dry_run:
            conn.executemany(
                "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                [(now, r["id"]) for r in empty_open],
            )
            conn.executemany(
                "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                [(now, r["id"]) for r in duplicate_open],
            )
            conn.executemany(
                "DELETE FROM sessions WHERE id = ?",
                [(r["id"],) for r in empty_ended],
            )

    return {
        "dry_run": dry_run,
        "closed_empty_open": len(empty_open),
        "closed_duplicate_open_contexts": len(duplicate_open),
        "deleted_empty_ended": len(empty_ended),
    }


def reconcile_mirror(*, dry_run: bool = True) -> dict[str, Any]:
    """Make the markdown mirror match SQLite canonical state."""
    db_ids = _db_fragment_ids()
    file_ids = _fragment_file_ids()
    orphan_ids = sorted(file_ids - db_ids)
    missing_ids = sorted(db_ids - file_ids)

    removed = 0
    written = 0
    if not dry_run:
        for fid in orphan_ids:
            path = config.FRAGMENTS_DIR / f"{fid}.md"
            if path.exists():
                path.unlink()
                removed += 1
        for fid in missing_ids:
            frag = frag_store.get(fid)
            if frag is None:
                continue
            obsidian_mirror.write_fragment(frag.to_dict())
            written += 1

    return {
        "dry_run": dry_run,
        "orphan_files": len(orphan_ids),
        "missing_files": len(missing_ids),
        "removed_orphan_files": removed,
        "written_missing_files": written,
        "orphan_sample": orphan_ids[:20],
        "missing_sample": missing_ids[:20],
    }
