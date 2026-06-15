from __future__ import annotations


def test_wiki_index_and_log_render_from_db(hippo_env):
    from hippocampus.wiki import index, log, storage

    project = storage.create_project("demo", "Demo", None, None)
    page = storage.upsert_page(
        project.id,
        page_type="concept",
        title="Durable Memory",
        markdown="# Durable Memory",
        frontmatter={
            "title": "Durable Memory",
            "type": "concept",
            "status": "current",
            "sources": [],
            "tags": ["memory"],
            "summary": "Compiled durable knowledge.",
        },
    )
    storage.append_log(project.id, kind="query-filed", title="Durable Memory", page_id=page.id)

    idx = index.refresh(project)
    log_page = log.refresh(project)
    assert "Durable Memory" in idx.markdown
    assert "query-filed | Durable Memory" in log_page.markdown

