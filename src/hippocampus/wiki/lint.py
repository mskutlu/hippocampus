"""Health checks for database-backed wiki state."""

from __future__ import annotations

from hippocampus.storage.db import get_ro_conn
from hippocampus.wiki import export, projects, storage
from hippocampus.wiki.models import WikiLintIssue


REQUIRED_META = ("title", "type", "status", "sources", "tags")


def run(project: str | None = None) -> dict:
    p, blocked = projects.require_project(project)
    if blocked:
        return blocked
    assert p is not None

    issues: list[WikiLintIssue] = []
    pages = storage.list_pages(p.id)
    paths = {pg.path for pg in pages}
    norm_seen: dict[str, str] = {}
    index_page = storage.get_page_by_path(p.id, "wiki/index.md")
    index_markdown = index_page.markdown if index_page else ""
    inbound: set[str] = set()
    with get_ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT l.target_path
            FROM wiki_links l
            JOIN wiki_pages p2 ON p2.id = l.page_id
            WHERE p2.project_id = ?
            """,
            (p.id,),
        ).fetchall()
        inbound = {r["target_path"] for r in rows}

    for pg in pages:
        for key in REQUIRED_META:
            if key not in pg.frontmatter:
                issues.append(WikiLintIssue("missing_metadata", f"{pg.path} missing {key}", "warn", pg.id, pg.path))
        if pg.normalized_title in norm_seen:
            issues.append(WikiLintIssue("duplicate_title", f"{pg.title} duplicates {norm_seen[pg.normalized_title]}", "error", pg.id, pg.path))
        norm_seen[pg.normalized_title] = pg.title
        if pg.page_type not in {"index", "log", "schema"} and pg.path[:-3] not in index_markdown:
            issues.append(WikiLintIssue("missing_index_entry", f"{pg.path} is not present in rendered index", "warn", pg.id, pg.path))
        if pg.page_type not in {"overview", "index", "log", "schema", "source"} and not pg.sources:
            issues.append(WikiLintIssue("uncited_page", f"{pg.path} has no sources", "warn", pg.id, pg.path))
        if pg.page_type not in {"overview", "index", "log", "schema", "source"} and pg.path not in inbound:
            issues.append(WikiLintIssue("orphan_page", f"{pg.path} has no inbound wikilinks", "warn", pg.id, pg.path))
        if pg.page_type != "schema" and "CONTRADICTION:" in pg.markdown.upper():
            issues.append(WikiLintIssue("contradiction_marker", f"{pg.path} contains contradiction marker", "warn", pg.id, pg.path))
        for target, _ in storage.extract_wikilinks(pg.markdown):
            if target not in paths:
                issues.append(WikiLintIssue("broken_wikilink", f"{pg.path} links to missing {target}", "warn", pg.id, pg.path))

    indexed = storage.get_page_by_path(p.id, "wiki/index.md")
    logged = storage.get_page_by_path(p.id, "wiki/log.md")
    if indexed is None:
        issues.append(WikiLintIssue("missing_index_entry", "wiki/index.md page record missing", "error"))
    if logged is None:
        issues.append(WikiLintIssue("missing_log_entry", "wiki/log.md page record missing", "error"))
    if not storage.list_log(p.id, limit=1):
        issues.append(WikiLintIssue("missing_log_entry", "wiki_log has no entries", "warn"))
    with get_ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.title
            FROM wiki_sources s
            LEFT JOIN wiki_log l ON l.source_id = s.id AND l.kind = 'ingest'
            WHERE s.project_id = ? AND l.id IS NULL
            """,
            (p.id,),
        ).fetchall()
        for row in rows:
            issues.append(WikiLintIssue("missing_log_entry", f"source {row['title']} has no ingest log entry", "warn"))
    for item in export.drift(p):
        issues.append(WikiLintIssue("materialization_drift", f"{item['path']} differs from DB", "warn", item["page_id"], item["path"]))

    return {
        "ok": not any(i.severity == "error" for i in issues),
        "project": p.to_dict(),
        "count": len(issues),
        "issues": [i.to_dict() for i in issues],
    }
