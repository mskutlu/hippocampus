"""Render database-backed wiki log entries."""

from __future__ import annotations

from datetime import datetime

from hippocampus.wiki.models import WikiProject
from hippocampus.wiki import storage


def _date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return iso[:10]


def render(project: WikiProject, limit: int = 500) -> str:
    entries = list(reversed(storage.list_log(project.id, limit=limit)))
    lines = [f"# {project.title} Wiki Log", ""]
    if not entries:
        lines.extend(["_(no log entries yet)_", ""])
        return "\n".join(lines)
    for e in entries:
        lines.append(f"## [{_date(e.created_at)}] {e.kind} | {e.title}")
        lines.append("")
        if e.details:
            lines.append(e.details.strip())
            lines.append("")
        if e.page_id or e.source_id:
            refs = []
            if e.page_id:
                refs.append(f"page={e.page_id}")
            if e.source_id:
                refs.append(f"source={e.source_id}")
            lines.append("`" + " ".join(refs) + "`")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def refresh(project: WikiProject):
    return storage.upsert_page(
        project.id,
        page_type="log",
        title="Log",
        path="wiki/log.md",
        markdown=render(project),
        frontmatter={
            "title": "Log",
            "type": "log",
            "project": project.project_key,
            "status": "current",
            "sources": [],
            "tags": ["log"],
        },
        status="current",
    )

