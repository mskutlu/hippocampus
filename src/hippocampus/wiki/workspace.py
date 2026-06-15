"""Project initialization for the LLM Wiki layer."""

from __future__ import annotations

from pathlib import Path

from hippocampus.wiki import index as wiki_index
from hippocampus.wiki import log as wiki_log
from hippocampus.wiki import projects, storage


SCHEMA_TEXT = """# LLM Wiki Schema

This project uses Hippocampus' database-backed LLM Wiki workflow.

## Rules

- Check `wiki_status` before ingesting, querying, linting, or filing answers.
- If the project wiki is missing, initialize it before continuing.
- SQLite is canonical. Exported markdown files are materialized views.
- Raw sources are immutable.
- Preserve source references on every source-backed claim.
- Update the wiki log after ingest, lint repair, or filed analysis.
- Ask the user before resolving contradictions that require judgment.
"""


def init_project(
    *,
    project: str | None = None,
    title: str | None = None,
    workspace_path: str | None = None,
    export_root: str | None = None,
    materialize: bool = False,
):
    project_key = projects.derive_project_key(project, workspace_path)
    resolved_export_root = export_root or str(projects.default_export_root(project_key))
    p = storage.create_project(
        project_key=project_key,
        title=title or project_key.replace("-", " ").title(),
        workspace_path=workspace_path or projects.current_workspace(),
        export_root=resolved_export_root,
    )
    overview = storage.upsert_page(
        p.id,
        page_type="overview",
        title="Overview",
        path="wiki/overview.md",
        markdown=f"# {p.title}\n\nProject wiki overview.\n",
        frontmatter={
            "title": "Overview",
            "type": "overview",
            "project": p.project_key,
            "status": "draft",
            "sources": [],
            "tags": ["overview"],
        },
    )
    schema = storage.upsert_page(
        p.id,
        page_type="schema",
        title="LLM-WIKI",
        path="schema/LLM-WIKI.md",
        markdown=SCHEMA_TEXT,
        frontmatter={
            "title": "LLM-WIKI",
            "type": "schema",
            "project": p.project_key,
            "status": "current",
            "sources": [],
            "tags": ["schema"],
        },
        status="current",
    )
    if not storage.list_log(p.id, limit=1):
        storage.append_log(p.id, kind="init", title=p.title, details="Initialized project wiki.")
    idx = wiki_index.refresh(p)
    log_page = wiki_log.refresh(p)
    exported: list[str] = []
    if materialize:
        from hippocampus.wiki import export

        exported = export.materialize(p)["written_paths"]
    return {
        "ok": True,
        "project": p.to_dict(),
        "pages": [overview.to_dict(), schema.to_dict(), idx.to_dict(), log_page.to_dict()],
        "materialized": materialize,
        "written_paths": exported,
    }


def ensure_dirs(root: str) -> None:
    base = Path(root)
    for rel in (
        "raw/inbox",
        "raw/assets",
        "wiki/sources",
        "wiki/entities",
        "wiki/concepts",
        "wiki/topics",
        "wiki/analyses",
        "schema",
    ):
        (base / rel).mkdir(parents=True, exist_ok=True)

