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
              SUM(CASE WHEN l.session_id IS NULL AND a.session_id IS NULL AND t.session_id IS NULL THEN 1 ELSE 0 END) AS no_activity
            FROM sessions s
            LEFT JOIN (SELECT DISTINCT session_id FROM session_ledger) l ON l.session_id = s.id
            LEFT JOIN (SELECT DISTINCT session_id FROM session_accesses) a ON a.session_id = s.id
            LEFT JOIN (SELECT DISTINCT session_id FROM session_transcript) t ON t.session_id = s.id
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
        wiki_projects = conn.execute(
            "SELECT COUNT(*) AS n FROM wiki_projects"
        ).fetchone()["n"]
        wiki_pages = conn.execute(
            "SELECT COUNT(*) AS n FROM wiki_pages"
        ).fetchone()["n"]
        wiki_sources = conn.execute(
            "SELECT COUNT(*) AS n FROM wiki_sources"
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
        "wiki": {
            "projects": int(wiki_projects or 0),
            "pages": int(wiki_pages or 0),
            "sources": int(wiki_sources or 0),
        },
        "duplicates": {
            "checked": include_duplicates,
            "candidates": duplicate_candidates,
        },
    }


def cleanup_sessions(*, dry_run: bool = True, max_age_hours: int | None = None) -> dict[str, Any]:
    """Close/delete no-activity sessions and close duplicate active contexts.

    V11: ended sessions whose only trace is `session_accesses` (hook injection
    noise) are also deleted once older than `max_age_hours`.
    """
    now = _now()
    age_hours = (
        int(config.get_setting("session_cleanup_age_hours") or 0)
        if max_age_hours is None
        else int(max_age_hours)
    )
    with get_conn() as conn:
        empty_open = conn.execute(
            """
            SELECT s.id
            FROM sessions s
            LEFT JOIN (SELECT DISTINCT session_id FROM session_ledger) l ON l.session_id = s.id
            LEFT JOIN (SELECT DISTINCT session_id FROM session_accesses) a ON a.session_id = s.id
            LEFT JOIN (SELECT DISTINCT session_id FROM session_transcript) t ON t.session_id = s.id
            WHERE s.ended_at IS NULL
              AND l.session_id IS NULL
              AND a.session_id IS NULL
              AND t.session_id IS NULL
            """
        ).fetchall()
        empty_ended = conn.execute(
            """
            SELECT s.id
            FROM sessions s
            LEFT JOIN (SELECT DISTINCT session_id FROM session_ledger) l ON l.session_id = s.id
            LEFT JOIN (SELECT DISTINCT session_id FROM session_accesses) a ON a.session_id = s.id
            LEFT JOIN (SELECT DISTINCT session_id FROM session_transcript) t ON t.session_id = s.id
            WHERE s.ended_at IS NOT NULL
              AND l.session_id IS NULL
              AND a.session_id IS NULL
              AND t.session_id IS NULL
            """
        ).fetchall()
        stale_ended = conn.execute(
            """
            SELECT s.id
            FROM sessions s
            WHERE s.ended_at IS NOT NULL
              AND s.started_at < strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)
              AND NOT EXISTS (SELECT 1 FROM session_ledger l WHERE l.session_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM session_transcript t WHERE t.session_id = s.id)
            """,
            (f"-{age_hours} hours",),
        ).fetchall() if age_hours > 0 else []
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
            conn.executemany(
                "DELETE FROM sessions WHERE id = ?",
                [(r["id"],) for r in stale_ended],
            )

    return {
        "dry_run": dry_run,
        "closed_empty_open": len(empty_open),
        "closed_duplicate_open_contexts": len(duplicate_open),
        "deleted_empty_ended": len(empty_ended),
        "deleted_stale_ended": len(stale_ended),
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


# ---------------------------------------------------------------------------
# V11 — audit, purge-noise, daily maintenance
# ---------------------------------------------------------------------------

AUDIT_THRESHOLDS: dict[str, float] = {
    "noise_fragments_max": 0,
    "saturated_ratio_max": 0.30,
    "embedding_coverage_min": 0.98,
    "stale_sessions_max": 100,
    "oversized_fragments_max": 0,
}


def _noise_pattern_sql() -> tuple[str, list[str]]:
    """WHERE clause matching fragments that carry hook/task markup."""
    clauses = ["content LIKE ?", "summary LIKE ?"]
    params = ["%<task-notification>%", "%<task-notification>%"]
    for marker in ("<system-reminder>", "<task-id>"):
        clauses.append("content LIKE ?")
        params.append(f"%{marker}%")
    return "(" + " OR ".join(clauses) + ")", params


def audit() -> dict[str, Any]:
    """Memory-health report. `ok` is False when any threshold is breached."""
    where, params = _noise_pattern_sql()
    max_chars = int(config.get_setting("distill_max_chars") or 4000)
    age_hours = int(config.get_setting("session_cleanup_age_hours") or 24)
    with get_ro_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM fragments").fetchone()["n"]
        noise = conn.execute(f"SELECT COUNT(*) AS n FROM fragments WHERE {where}", params).fetchone()["n"]
        saturated = conn.execute("SELECT COUNT(*) AS n FROM fragments WHERE confidence >= 1.0").fetchone()["n"]
        oversized = conn.execute(
            "SELECT COUNT(*) AS n FROM fragments WHERE length(content) > ?", (max_chars,)
        ).fetchone()["n"]
        embedded = conn.execute(
            "SELECT COUNT(*) AS n FROM fragment_embeddings e JOIN fragments f ON f.id = e.fragment_id"
        ).fetchone()["n"]
        never_accessed = conn.execute("SELECT COUNT(*) AS n FROM fragments WHERE accessed = 0").fetchone()["n"]
        sessions_total = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        stale_sessions = conn.execute(
            """
            SELECT COUNT(*) AS n FROM sessions s
            WHERE s.started_at < strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)
              AND NOT EXISTS (SELECT 1 FROM session_ledger l WHERE l.session_id = s.id)
            """,
            (f"-{age_hours} hours",),
        ).fetchone()["n"]
        feedback_rows = conn.execute("SELECT COUNT(*) AS n FROM feedback_log").fetchone()["n"]
        boosts_7d = conn.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT fragment_id) AS d FROM feedback_log "
            "WHERE kind = 'boost' AND created_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-7 days')"
        ).fetchone()
        top_accessed = conn.execute(
            "SELECT id, accessed FROM fragments ORDER BY accessed DESC LIMIT 1"
        ).fetchone()
        distinct_tags = conn.execute("SELECT COUNT(DISTINCT tag) AS n FROM fragment_tags").fetchone()["n"]
        db_bytes = conn.execute("SELECT page_count * page_size AS b FROM pragma_page_count(), pragma_page_size()").fetchone()["b"]

    saturated_ratio = (saturated / total) if total else 0.0
    embedding_coverage = (embedded / total) if total else 1.0
    metrics = {
        "fragments_total": total,
        "noise_fragments": noise,
        "saturated_fragments": saturated,
        "saturated_ratio": round(saturated_ratio, 4),
        "oversized_fragments": oversized,
        "never_accessed_fragments": never_accessed,
        "embedding_coverage": round(embedding_coverage, 4),
        "distinct_tags": distinct_tags,
        "sessions_total": sessions_total,
        "stale_sessions": stale_sessions,
        "feedback_rows": feedback_rows,
        "boosts_last_7d": int(boosts_7d["n"] or 0),
        "distinct_fragments_boosted_7d": int(boosts_7d["d"] or 0),
        "top_accessed": dict(top_accessed) if top_accessed else None,
        "db_bytes": int(db_bytes or 0),
    }
    breaches: list[str] = []
    if noise > AUDIT_THRESHOLDS["noise_fragments_max"]:
        breaches.append(f"noise_fragments={noise} > {AUDIT_THRESHOLDS['noise_fragments_max']}")
    if saturated_ratio > AUDIT_THRESHOLDS["saturated_ratio_max"]:
        breaches.append(f"saturated_ratio={saturated_ratio:.2f} > {AUDIT_THRESHOLDS['saturated_ratio_max']}")
    if total and embedding_coverage < AUDIT_THRESHOLDS["embedding_coverage_min"]:
        breaches.append(f"embedding_coverage={embedding_coverage:.2f} < {AUDIT_THRESHOLDS['embedding_coverage_min']}")
    if stale_sessions > AUDIT_THRESHOLDS["stale_sessions_max"]:
        breaches.append(f"stale_sessions={stale_sessions} > {AUDIT_THRESHOLDS['stale_sessions_max']}")
    if oversized > AUDIT_THRESHOLDS["oversized_fragments_max"]:
        breaches.append(f"oversized_fragments={oversized} > {AUDIT_THRESHOLDS['oversized_fragments_max']}")
    return {"ok": not breaches, "breaches": breaches, "metrics": metrics, "thresholds": AUDIT_THRESHOLDS}


def purge_noise(*, dry_run: bool = True) -> dict[str, Any]:
    """Delete markup fragments and shrink oversized session summaries.

    Takes a backup first unless dry_run. Oversized session-summary fragments
    are re-rendered from their ledger when the source session still exists,
    otherwise cut at `distill_max_chars`.
    """
    from hippocampus.storage import backup, ledger as ledger_store

    where, params = _noise_pattern_sql()
    max_chars = int(config.get_setting("distill_max_chars") or 4000)
    with get_ro_conn() as conn:
        noise_ids = [r["id"] for r in conn.execute(f"SELECT id FROM fragments WHERE {where}", params).fetchall()]
        oversized = [
            dict(r)
            for r in conn.execute(
                "SELECT id, source_type, source_ref, summary, content FROM fragments "
                "WHERE length(content) > ?",
                (max_chars,),
            ).fetchall()
        ]

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "deleted_noise": len(noise_ids),
        "rerendered": 0,
        "truncated": 0,
        "backup": None,
    }
    if dry_run:
        return result

    result["backup"] = backup.create(prefix="pre-purge")["path"]
    for fid in noise_ids:
        frag_store.delete(fid)

    from hippocampus.mcp import tools as T

    for row in oversized:
        if row["id"] in noise_ids:
            continue
        new_content: str | None = None
        if row["source_type"] == "session-summary" and row["source_ref"]:
            try:
                entries = ledger_store.current_entries(row["source_ref"])
            except Exception:
                entries = []
            if entries:
                new_content = T._render_ledger_as_fragment(entries, explicit_summary=row["summary"])
                result["rerendered"] += 1
        if new_content is None:
            new_content = row["content"][: max_chars - 1].rstrip() + "…"
            result["truncated"] += 1
        frag_store.update_fields(row["id"], content=new_content)
        try:
            from hippocampus.embeddings import search as semantic_search

            semantic_search.upsert_for_fragment(row["id"])
        except Exception:
            pass
    return result


def run_daily_maintenance(*, dry_run: bool = False) -> dict[str, Any]:
    """Archive + session cleanup + feedback prune + reindex, in that order."""
    from hippocampus.dynamics import archive as archive_dyn
    from hippocampus.storage import feedback

    out: dict[str, Any] = {"dry_run": dry_run}
    out["archive"] = archive_dyn.run_archive_cycle(dry_run=dry_run).as_dict()
    out["sessions"] = cleanup_sessions(dry_run=dry_run)
    days = int(config.get_setting("feedback_retention_days") or 0)
    out["feedback_pruned"] = 0 if dry_run else feedback.prune(days)
    if not dry_run:
        from hippocampus.storage import db as _db

        out["vacuum_reclaimed_bytes"] = _db.vacuum()
    try:
        from hippocampus.embeddings import search as semantic_search

        out["reindex"] = {"skipped": True} if dry_run else semantic_search.reindex(force=False)
    except Exception as exc:  # embeddings optional
        out["reindex"] = {"error": str(exc)}
    return out
