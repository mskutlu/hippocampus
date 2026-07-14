from __future__ import annotations


def test_wiki_fts_ranks_titles_and_tracks_updates(hippo_env):
    from hippocampus.wiki import storage

    project = storage.create_project("demo", "Demo", None, None)
    title_hit = storage.upsert_page(
        project.id,
        page_type="topic",
        title="Quasar Guide",
        markdown="A short astronomy note.",
    )
    storage.upsert_page(
        project.id,
        page_type="topic",
        title="Astronomy",
        markdown="This page mentions quasar once.",
    )

    hits = storage.search_pages(project.id, "quasar?", limit=10)

    assert hits[0].id == title_hit.id

    storage.upsert_page(
        project.id,
        page_type="topic",
        title="Quasar Guide",
        markdown="A short astronomy note about magnetars.",
    )
    updated = storage.search_pages(project.id, "magnetars", limit=10)
    assert [page.id for page in updated] == [title_hit.id]


def test_page_updates_preserve_all_source_links(hippo_env):
    from hippocampus.wiki import storage

    project = storage.create_project("demo", "Demo", None, None)
    first, _ = storage.create_source(
        project.id,
        title="First",
        source_type="markdown",
        source_ref="/tmp/first.md",
        content_hash="first",
    )
    second, _ = storage.create_source(
        project.id,
        title="Second",
        source_type="markdown",
        source_ref="/tmp/second.md",
        content_hash="second",
    )
    storage.upsert_page(
        project.id,
        page_type="analysis",
        title="Answer",
        markdown="First revision",
        source_ids=[first.id],
    )
    page = storage.upsert_page(
        project.id,
        page_type="analysis",
        title="Answer",
        markdown="Second revision",
        source_ids=[second.id],
    )

    assert page.sources == sorted([first.id, second.id])
    assert page.frontmatter["sources"] == page.sources
