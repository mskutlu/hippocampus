"""Source ingest for database-backed wiki projects."""

from __future__ import annotations

import hashlib
from pathlib import Path

from hippocampus.wiki import index as wiki_index
from hippocampus.wiki import log as wiki_log
from hippocampus.wiki import naming, projects, storage


def _read_source(path: str) -> tuple[str, str]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return p.name, text


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _summary(text: str, limit: int = 500) -> str:
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def ingest(raw_path: str, *, project: str | None = None, dry_run: bool = False, materialize: bool = False) -> dict:
    p, blocked = projects.require_project(project)
    if blocked:
        return blocked
    assert p is not None

    name, text = _read_source(raw_path)
    title = naming.title_from_path(name)
    content_hash = _hash(text)
    plan = {
        "ok": True,
        "dry_run": dry_run,
        "project": p.to_dict(),
        "source": {"title": title, "path": raw_path, "content_hash": content_hash},
        "planned_pages": [f"wiki/sources/{naming.filename_for_title(title)}"],
    }
    if dry_run:
        return plan

    src, created = storage.create_source(
        p.id,
        title=title,
        source_type="markdown" if str(raw_path).lower().endswith((".md", ".markdown")) else "text",
        source_ref=str(Path(raw_path).resolve()),
        content_hash=content_hash,
        metadata={"bytes": len(text.encode("utf-8"))},
    )
    if not created:
        return {**plan, "created": False, "duplicate": True, "source_record": src.to_dict()}

    source_md = "\n".join([
        f"# {title}",
        "",
        "## Summary",
        "",
        _summary(text),
        "",
        "## Source Reference",
        "",
        f"- `{raw_path}`",
        "",
        "## Extracted Text",
        "",
        text.strip(),
        "",
    ])
    source_page = storage.upsert_page(
        p.id,
        page_type="source",
        title=title,
        markdown=source_md,
        frontmatter={
            "title": title,
            "type": "source",
            "project": p.project_key,
            "status": "current",
            "sources": [src.id],
            "tags": ["source"],
            "summary": _summary(text, 160),
        },
        status="current",
        source_ids=[src.id],
    )
    topic_page = storage.upsert_page(
        p.id,
        page_type="topic",
        title="Ingested Sources",
        markdown=(
            "# Ingested Sources\n\n"
            "This topic collects sources ingested into the project wiki.\n\n"
            f"- [[{source_page.path[:-3]}|{title}]]\n"
        ),
        frontmatter={
            "title": "Ingested Sources",
            "type": "topic",
            "project": p.project_key,
            "status": "current",
            "sources": [src.id],
            "tags": ["topic", "sources"],
            "summary": "Sources ingested into this wiki.",
        },
        status="current",
        source_ids=[src.id],
    )
    storage.append_log(
        p.id,
        kind="ingest",
        title=title,
        details=f"Ingested `{raw_path}` into `{source_page.path}`.",
        page_id=source_page.id,
        source_id=src.id,
    )
    idx = wiki_index.refresh(p)
    log_page = wiki_log.refresh(p)
    written: list[str] = []
    if materialize:
        from hippocampus.wiki import export

        written = export.materialize(p)["written_paths"]
    return {
        **plan,
        "created": True,
        "duplicate": False,
        "source_record": src.to_dict(),
        "pages_created": [source_page.to_dict(), topic_page.to_dict()],
        "pages_updated": [idx.to_dict(), log_page.to_dict()],
        "materialized": materialize,
        "written_paths": written,
    }

