"""Obsidian vault mirror.

For every fragment in SQLite, we maintain a markdown file at
    {VAULT_HOME}/Fragments/<fragment_id>.md

with YAML frontmatter mirroring the DB row. Archive moves the file to
    {VAULT_HOME}/Fragments/.archive/<fragment_id>.md

The sync is one-way (DB → vault). Hand edits to the markdown files are NOT
pulled back in V1 — they'll be overwritten on the next mutation. V1.1 may add
a `hippo pull` command if the user asks for it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

from hippocampus import config


def _path_for(fragment_id: str) -> Path:
    return config.FRAGMENTS_DIR / f"{fragment_id}.md"


def _archive_path_for(fragment_id: str) -> Path:
    return config.FRAGMENTS_ARCHIVE_DIR / f"{fragment_id}.md"


def _render(frag: dict[str, Any]) -> str:
    fm: dict[str, Any] = {
        "id": frag["id"],
        "confidence": float(frag["confidence"]),
        "accessed": int(frag["accessed"]),
        "last_accessed_at": frag.get("last_accessed_at"),
        "created_at": frag.get("created_at"),
        "updated_at": frag.get("updated_at"),
        "pinned": bool(frag.get("pinned", False)),
        "source_type": frag.get("source_type", "manual"),
        "source_ref": frag.get("source_ref"),
        "tags": sorted(frag.get("tags", []) or []),
        "associated_with": frag.get("associated_with", []) or [],
    }
    fm_str = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    summary = (frag.get("summary") or "").strip()
    content = (frag.get("content") or "").strip()
    body = []
    body.append("---")
    body.append(fm_str)
    body.append("---")
    body.append("")
    if summary:
        body.append("# Summary")
        body.append("")
        body.append(summary)
        body.append("")
    body.append("# Content")
    body.append("")
    body.append(content)
    body.append("")
    return "\n".join(body)


def write_fragment(frag: dict[str, Any]) -> Path:
    """Atomically write (or overwrite) a fragment's mirror file."""
    config.ensure_dirs()
    path = _path_for(frag["id"])
    tmp = path.with_suffix(path.suffix + ".tmp")
    rendered = _render(frag)
    tmp.write_text(rendered, encoding="utf-8")
    tmp.replace(path)
    return path


def delete_fragment_mirror(fragment_id: str) -> bool:
    path = _path_for(fragment_id)
    if path.exists():
        path.unlink()
        return True
    return False


def archive_fragment_mirror(fragment_id: str) -> bool:
    """Move the mirror file into the .archive/ subdirectory. Idempotent."""
    config.ensure_dirs()
    src = _path_for(fragment_id)
    dst = _archive_path_for(fragment_id)
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return True
    return False


def bootstrap_hooks() -> None:
    """Register mirror callbacks with the fragments module."""
    from hippocampus.storage import fragments

    fragments.register_mirror_hooks(
        write=write_fragment,
        delete=delete_fragment_mirror,
        archive=archive_fragment_mirror,
    )
