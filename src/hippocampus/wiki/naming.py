"""Title and path normalization for wiki pages."""

from __future__ import annotations

import re
from pathlib import Path


PAGE_DIRS = {
    "source": "wiki/sources",
    "entity": "wiki/entities",
    "concept": "wiki/concepts",
    "topic": "wiki/topics",
    "analysis": "wiki/analyses",
    "overview": "wiki",
    "index": "wiki",
    "log": "wiki",
    "schema": "schema",
}


def title_from_path(path: str | Path) -> str:
    p = Path(path)
    return p.stem.replace("-", " ").replace("_", " ").strip().title() or "Untitled"


def normalize_title(title: str, page_type: str | None = None) -> str:
    base = (title or "").strip().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    if not base:
        base = "untitled"
    return f"{page_type}:{base}" if page_type else base


def filename_for_title(title: str) -> str:
    words = re.split(r"[^A-Za-z0-9]+", (title or "").strip())
    cleaned = "-".join(w for w in words if w)
    return (cleaned or "Untitled") + ".md"


def path_for_page(page_type: str, title: str) -> str:
    if page_type == "index":
        return "wiki/index.md"
    if page_type == "log":
        return "wiki/log.md"
    if page_type == "overview":
        return "wiki/overview.md"
    if page_type == "schema":
        return "schema/LLM-WIKI.md"
    base = PAGE_DIRS.get(page_type, "wiki")
    return f"{base}/{filename_for_title(title)}"

