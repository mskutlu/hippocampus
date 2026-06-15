"""Materialize database-backed wiki pages to markdown files."""

from __future__ import annotations

import hashlib
from pathlib import Path

from hippocampus.wiki import frontmatter, storage, workspace
from hippocampus.wiki.models import WikiProject


def _render_page(page) -> str:
    meta = dict(page.frontmatter)
    meta.setdefault("title", page.title)
    meta.setdefault("type", page.page_type)
    meta.setdefault("status", page.status)
    meta.setdefault("sources", page.sources)
    return frontmatter.render_frontmatter(meta, page.markdown)


def _write_if_changed(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if hashlib.sha256(old.encode()).hexdigest() == hashlib.sha256(text.encode()).hexdigest():
            return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return True


def materialize(project: WikiProject) -> dict:
    if not project.export_root:
        return {"ok": False, "reason": "missing_export_root", "written_paths": []}
    root = Path(project.export_root)
    workspace.ensure_dirs(str(root))
    written: list[str] = []
    for page in storage.list_pages(project.id):
        out = root / page.path
        if _write_if_changed(out, _render_page(page)):
            written.append(str(out))
    return {"ok": True, "project": project.to_dict(), "export_root": str(root), "written_paths": written}


def drift(project: WikiProject) -> list[dict]:
    if not project.export_root:
        return []
    root = Path(project.export_root)
    issues: list[dict] = []
    for page in storage.list_pages(project.id):
        path = root / page.path
        if not path.exists():
            continue
        exported = path.read_text(encoding="utf-8")
        expected = _render_page(page)
        if hashlib.sha256(exported.encode()).hexdigest() != hashlib.sha256(expected.encode()).hexdigest():
            issues.append({"page_id": page.id, "path": page.path})
    return issues

