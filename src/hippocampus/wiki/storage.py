"""SQLite CRUD for database-backed wiki state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from ulid import ULID

from hippocampus.storage.db import get_conn, get_ro_conn
from hippocampus.wiki.models import WikiLogEntry, WikiPage, WikiProject, WikiSource
from hippocampus.wiki.naming import normalize_title, path_for_page


def _id(prefix: str) -> str:
    return f"{prefix}_{ULID()}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _json(data: dict[str, Any] | None) -> str:
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True)


def _load_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _row_project(row) -> WikiProject:
    return WikiProject(
        id=row["id"],
        project_key=row["project_key"],
        title=row["title"],
        workspace_path=row["workspace_path"],
        export_root=row["export_root"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _page_sources(conn, page_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT source_id FROM wiki_page_sources WHERE page_id = ? ORDER BY source_id",
        (page_id,),
    ).fetchall()
    return [r["source_id"] for r in rows]


def _row_page(row, conn=None) -> WikiPage:
    sources = _page_sources(conn, row["id"]) if conn is not None else []
    return WikiPage(
        id=row["id"],
        project_id=row["project_id"],
        page_type=row["page_type"],
        title=row["title"],
        normalized_title=row["normalized_title"],
        path=row["path"],
        markdown=row["markdown"],
        frontmatter=_load_json(row["frontmatter_json"]),
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        sources=sources,
    )


def _row_source(row) -> WikiSource:
    return WikiSource(
        id=row["id"],
        project_id=row["project_id"],
        title=row["title"],
        source_type=row["source_type"],
        source_ref=row["source_ref"],
        content_hash=row["content_hash"],
        metadata=_load_json(row["metadata_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_log(row) -> WikiLogEntry:
    return WikiLogEntry(
        id=row["id"],
        project_id=row["project_id"],
        kind=row["kind"],
        title=row["title"],
        details=row["details"],
        page_id=row["page_id"],
        source_id=row["source_id"],
        metadata=_load_json(row["metadata_json"]),
        created_at=row["created_at"],
    )


def create_project(project_key: str, title: str, workspace_path: str | None, export_root: str | None) -> WikiProject:
    existing = get_project_by_key(project_key)
    if existing:
        return existing
    pid = _id("wproj")
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO wiki_projects(id, project_key, title, workspace_path, export_root, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (pid, project_key, title, workspace_path, export_root, now, now),
        )
        row = conn.execute("SELECT * FROM wiki_projects WHERE id = ?", (pid,)).fetchone()
    return _row_project(row)


def get_project_by_key(project_key: str) -> WikiProject | None:
    with get_ro_conn() as conn:
        row = conn.execute("SELECT * FROM wiki_projects WHERE project_key = ?", (project_key,)).fetchone()
    return _row_project(row) if row else None


def list_projects(limit: int = 100) -> list[WikiProject]:
    with get_ro_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wiki_projects ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_project(r) for r in rows]


def upsert_page(
    project_id: str,
    *,
    page_type: str,
    title: str,
    markdown: str,
    frontmatter: dict[str, Any] | None = None,
    path: str | None = None,
    status: str = "draft",
    source_ids: Iterable[str] = (),
) -> WikiPage:
    norm = normalize_title(title, page_type)
    resolved_path = path or path_for_page(page_type, title)
    now = _now()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM wiki_pages WHERE project_id = ? AND normalized_title = ?",
            (project_id, norm),
        ).fetchone()
        if row:
            page_id = row["id"]
            conn.execute(
                """
                UPDATE wiki_pages
                SET page_type = ?, title = ?, path = ?, markdown = ?, frontmatter_json = ?,
                    status = ?, updated_at = ?
                WHERE id = ?
                """,
                (page_type, title, resolved_path, markdown, _json(frontmatter), status, now, page_id),
            )
        else:
            page_id = _id("wpage")
            conn.execute(
                """
                INSERT INTO wiki_pages
                    (id, project_id, page_type, title, normalized_title, path, markdown,
                     frontmatter_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (page_id, project_id, page_type, title, norm, resolved_path, markdown,
                 _json(frontmatter), status, now, now),
            )
        conn.execute("DELETE FROM wiki_page_sources WHERE page_id = ?", (page_id,))
        for sid in source_ids:
            conn.execute(
                "INSERT OR IGNORE INTO wiki_page_sources(page_id, source_id) VALUES (?, ?)",
                (page_id, sid),
            )
        replace_links(conn, page_id, extract_wikilinks(markdown))
        conn.execute("UPDATE wiki_projects SET updated_at = ? WHERE id = ?", (now, project_id))
        page_row = conn.execute("SELECT * FROM wiki_pages WHERE id = ?", (page_id,)).fetchone()
        page = _row_page(page_row, conn)
    return page


def get_page(project_id: str, page_id: str) -> WikiPage | None:
    with get_ro_conn() as conn:
        row = conn.execute(
            "SELECT * FROM wiki_pages WHERE project_id = ? AND id = ?",
            (project_id, page_id),
        ).fetchone()
        return _row_page(row, conn) if row else None


def get_page_by_path(project_id: str, path: str) -> WikiPage | None:
    with get_ro_conn() as conn:
        row = conn.execute(
            "SELECT * FROM wiki_pages WHERE project_id = ? AND path = ?",
            (project_id, path),
        ).fetchone()
        return _row_page(row, conn) if row else None


def list_pages(project_id: str, page_type: str | None = None, limit: int = 1000) -> list[WikiPage]:
    with get_ro_conn() as conn:
        if page_type:
            rows = conn.execute(
                "SELECT * FROM wiki_pages WHERE project_id = ? AND page_type = ? ORDER BY path LIMIT ?",
                (project_id, page_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM wiki_pages WHERE project_id = ? ORDER BY path LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [_row_page(r, conn) for r in rows]


def create_source(
    project_id: str,
    *,
    title: str,
    source_type: str,
    source_ref: str | None,
    content_hash: str | None,
    metadata: dict[str, Any] | None = None,
) -> tuple[WikiSource, bool]:
    now = _now()
    if content_hash:
        with get_ro_conn() as conn:
            row = conn.execute(
                "SELECT * FROM wiki_sources WHERE project_id = ? AND content_hash = ?",
                (project_id, content_hash),
            ).fetchone()
            if row:
                return _row_source(row), False
    sid = _id("wsrc")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO wiki_sources
                (id, project_id, title, source_type, source_ref, content_hash, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sid, project_id, title, source_type, source_ref, content_hash, _json(metadata), now, now),
        )
        row = conn.execute("SELECT * FROM wiki_sources WHERE id = ?", (sid,)).fetchone()
    return _row_source(row), True


def get_source(project_id: str, source_id: str) -> WikiSource | None:
    with get_ro_conn() as conn:
        row = conn.execute(
            "SELECT * FROM wiki_sources WHERE project_id = ? AND id = ?",
            (project_id, source_id),
        ).fetchone()
    return _row_source(row) if row else None


def list_sources(project_id: str, limit: int = 1000) -> list[WikiSource]:
    with get_ro_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wiki_sources WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    return [_row_source(r) for r in rows]


def append_log(
    project_id: str,
    *,
    kind: str,
    title: str,
    details: str | None = None,
    page_id: str | None = None,
    source_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> WikiLogEntry:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO wiki_log(project_id, kind, title, details, page_id, source_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, kind, title, details, page_id, source_id, _json(metadata)),
        )
        row = conn.execute("SELECT * FROM wiki_log WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_log(row)


def list_log(project_id: str, limit: int = 100) -> list[WikiLogEntry]:
    with get_ro_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wiki_log WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    return [_row_log(r) for r in rows]


def extract_wikilinks(markdown: str) -> list[tuple[str, str | None]]:
    import re

    out: list[tuple[str, str | None]] = []
    for raw in re.findall(r"\[\[([^\]]+)\]\]", markdown or ""):
        target, _, label = raw.partition("|")
        target = target.strip()
        if target:
            path = target if target.endswith(".md") else f"{target}.md"
            out.append((path, label.strip() or None))
    return out


def replace_links(conn, page_id: str, links: list[tuple[str, str | None]]) -> None:
    conn.execute("DELETE FROM wiki_links WHERE page_id = ?", (page_id,))
    for target_path, target_title in links:
        conn.execute(
            "INSERT OR IGNORE INTO wiki_links(page_id, target_path, target_title) VALUES (?, ?, ?)",
            (page_id, target_path, target_title),
        )


def search_pages(project_id: str, query: str, limit: int = 10) -> list[WikiPage]:
    pattern = f"%{query.strip()}%" if query.strip() else "%"
    with get_ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM wiki_pages
            WHERE project_id = ?
              AND page_type NOT IN ('index', 'log', 'schema')
              AND (title LIKE ? OR markdown LIKE ? OR path LIKE ?)
            ORDER BY
              CASE WHEN title LIKE ? THEN 0 ELSE 1 END,
              updated_at DESC
            LIMIT ?
            """,
            (project_id, pattern, pattern, pattern, pattern, limit),
        ).fetchall()
        return [_row_page(r, conn) for r in rows]
