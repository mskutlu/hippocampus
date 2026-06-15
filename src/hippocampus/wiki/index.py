"""Render a database-backed wiki index as markdown."""

from __future__ import annotations

from collections import defaultdict

from hippocampus.wiki.models import WikiPage, WikiProject
from hippocampus.wiki import storage


def render(project: WikiProject) -> str:
    pages = [p for p in storage.list_pages(project.id) if p.page_type not in {"index", "log", "schema"}]
    grouped: dict[str, list[WikiPage]] = defaultdict(list)
    for p in pages:
        grouped[p.page_type].append(p)

    lines = [
        f"# {project.title} Wiki Index",
        "",
        f"Project key: `{project.project_key}`",
        "",
    ]
    for page_type in ("overview", "source", "entity", "concept", "topic", "analysis"):
        items = sorted(grouped.get(page_type, []), key=lambda p: p.title.lower())
        if not items:
            continue
        lines.append(f"## {page_type.title()}s")
        lines.append("")
        for p in items:
            summary = str(p.frontmatter.get("summary") or "").strip()
            suffix = f" - {summary}" if summary else ""
            lines.append(f"- [[{p.path[:-3]}|{p.title}]]{suffix}")
        lines.append("")
    if len(lines) <= 4:
        lines.extend(["_(no wiki pages yet)_", ""])
    return "\n".join(lines).rstrip() + "\n"


def refresh(project: WikiProject):
    page = storage.upsert_page(
        project.id,
        page_type="index",
        title="Index",
        path="wiki/index.md",
        markdown=render(project),
        frontmatter={
            "title": "Index",
            "type": "index",
            "project": project.project_key,
            "status": "current",
            "sources": [],
            "tags": ["index"],
        },
        status="current",
    )
    return page

