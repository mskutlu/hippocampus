"""Query and file-answer helpers for database-backed wiki pages."""

from __future__ import annotations

import sys

from hippocampus.wiki import index as wiki_index
from hippocampus.wiki import log as wiki_log
from hippocampus.wiki import projects, storage


def _snippet(text: str, limit: int = 500) -> str:
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def query(question: str, *, project: str | None = None, limit: int = 8) -> dict:
    p, blocked = projects.require_project(project)
    if blocked:
        return blocked
    assert p is not None

    hits = storage.search_pages(p.id, question, limit=limit)
    if not hits:
        hits = storage.list_pages(p.id, limit=limit)
    return {
        "ok": True,
        "project": p.to_dict(),
        "question": question,
        "count": len(hits),
        "pages": [
            {
                "id": page.id,
                "title": page.title,
                "type": page.page_type,
                "path": page.path,
                "sources": page.sources,
                "snippet": _snippet(page.markdown),
            }
            for page in hits
        ],
    }


def file_answer(
    title: str,
    markdown: str | None = None,
    *,
    project: str | None = None,
    materialize: bool = False,
) -> dict:
    p, blocked = projects.require_project(project)
    if blocked:
        return blocked
    assert p is not None

    body = markdown if markdown is not None else sys.stdin.read()
    page = storage.upsert_page(
        p.id,
        page_type="analysis",
        title=title,
        markdown=body,
        frontmatter={
            "title": title,
            "type": "analysis",
            "project": p.project_key,
            "status": "current",
            "sources": [],
            "tags": ["analysis"],
            "summary": _snippet(body, 160),
        },
        status="current",
    )
    storage.append_log(p.id, kind="query-filed", title=title, details=f"Filed analysis `{page.path}`.", page_id=page.id)
    idx = wiki_index.refresh(p)
    log_page = wiki_log.refresh(p)
    written: list[str] = []
    if materialize:
        from hippocampus.wiki import export

        written = export.materialize(p)["written_paths"]
    return {
        "ok": True,
        "project": p.to_dict(),
        "page": page.to_dict(),
        "pages_updated": [idx.to_dict(), log_page.to_dict()],
        "materialized": materialize,
        "written_paths": written,
    }

