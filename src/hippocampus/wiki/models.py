"""Dataclasses used by the database-backed wiki layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WikiProject:
    id: str
    project_key: str
    title: str
    workspace_path: str | None
    export_root: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class WikiSource:
    id: str
    project_id: str
    title: str
    source_type: str
    source_ref: str | None
    content_hash: str | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["metadata"] = dict(self.metadata)
        return d


@dataclass
class WikiPage:
    id: str
    project_id: str
    page_type: str
    title: str
    normalized_title: str
    path: str
    markdown: str
    frontmatter: dict[str, Any]
    status: str
    created_at: str
    updated_at: str
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["frontmatter"] = dict(self.frontmatter)
        d["sources"] = list(self.sources)
        d["type"] = self.page_type
        return d


@dataclass
class WikiLogEntry:
    id: int
    project_id: str
    kind: str
    title: str
    details: str | None
    page_id: str | None
    source_id: str | None
    metadata: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["metadata"] = dict(self.metadata)
        return d


@dataclass
class WikiLintIssue:
    code: str
    message: str
    severity: str = "warn"
    page_id: str | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def blocked_not_initialized(project_key: str, *, workspace_path: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "blocked": True,
        "reason": "wiki_not_initialized",
        "project_key": project_key,
        "workspace_path": workspace_path,
        "next_step": f"Run `hippo wiki init --project {project_key}` before continuing.",
    }
