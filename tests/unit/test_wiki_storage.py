from __future__ import annotations

import pytest


def test_wiki_storage_crud_and_unique_constraint(hippo_env):
    from hippocampus.wiki import storage

    project = storage.create_project("demo", "Demo", "/tmp/demo", "/tmp/export")
    page = storage.upsert_page(
        project.id,
        page_type="concept",
        title="Durable Memory",
        markdown="# Durable Memory\n",
        frontmatter={"title": "Durable Memory", "type": "concept", "status": "draft", "sources": [], "tags": []},
    )
    assert storage.get_page(project.id, page.id).title == "Durable Memory"

    updated = storage.upsert_page(
        project.id,
        page_type="concept",
        title="Durable Memory",
        markdown="# Durable Memory\n\nUpdated.\n",
        frontmatter={"title": "Durable Memory", "type": "concept", "status": "current", "sources": [], "tags": []},
        status="current",
    )
    assert updated.id == page.id
    assert "Updated" in updated.markdown

    with pytest.raises(Exception):
        storage.upsert_page(
            project.id,
            page_type="topic",
            title="Durable Memory",
            path=page.path,
            markdown="duplicate path",
            frontmatter={"title": "Durable Memory", "type": "topic", "status": "draft", "sources": [], "tags": []},
        )


def test_wiki_source_duplicate_by_hash(hippo_env):
    from hippocampus.wiki import storage

    project = storage.create_project("demo", "Demo", None, None)
    first, created_first = storage.create_source(
        project.id,
        title="Source",
        source_type="markdown",
        source_ref="/tmp/source.md",
        content_hash="abc",
    )
    second, created_second = storage.create_source(
        project.id,
        title="Source Again",
        source_type="markdown",
        source_ref="/tmp/source.md",
        content_hash="abc",
    )
    assert created_first is True
    assert created_second is False
    assert second.id == first.id

