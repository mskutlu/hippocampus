"""Project resolution and initialization gates for wiki operations."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from hippocampus import config
from hippocampus.wiki import storage
from hippocampus.wiki.models import WikiProject, blocked_not_initialized


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip()).strip("-").lower()
    return s or "default"


def current_workspace() -> str | None:
    raw = os.environ.get("HIPPOCAMPUS_CWD") or os.environ.get("PWD") or os.getcwd()
    try:
        return str(Path(raw).resolve())
    except OSError:
        return raw


def derive_project_key(project: str | None = None, workspace_path: str | None = None) -> str:
    if project and project.strip():
        return _slug(project)
    workspace = workspace_path or current_workspace()
    if not workspace:
        return "default"
    p = Path(workspace)
    digest = hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:8]
    return f"{_slug(p.name)}-{digest}"


def default_export_root(project_key: str) -> Path:
    root = config.get_setting("wiki_default_export_root")
    base = Path(root) if root else config.VAULT_HOME / "Wiki"
    return base / project_key


def get_project(project: str | None = None, workspace_path: str | None = None) -> WikiProject | None:
    key = derive_project_key(project, workspace_path)
    return storage.get_project_by_key(key)


def require_project(project: str | None = None, workspace_path: str | None = None) -> tuple[WikiProject | None, dict | None]:
    key = derive_project_key(project, workspace_path)
    p = storage.get_project_by_key(key)
    if p is None:
        return None, blocked_not_initialized(key, workspace_path=workspace_path or current_workspace())
    return p, None

