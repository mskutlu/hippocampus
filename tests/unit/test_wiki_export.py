from __future__ import annotations


def test_wiki_export_materializes_pages_and_detects_drift(hippo_env):
    from hippocampus.wiki import export, storage

    root = hippo_env["vault"] / "Wiki" / "demo"
    project = storage.create_project("demo", "Demo", None, str(root))
    page = storage.upsert_page(
        project.id,
        page_type="overview",
        title="Overview",
        markdown="# Overview",
        frontmatter={"title": "Overview", "type": "overview", "status": "draft", "sources": [], "tags": []},
    )
    out = export.materialize(project)
    assert out["ok"] is True
    target = root / page.path
    assert target.exists()
    assert export.drift(project) == []

    target.write_text("changed", encoding="utf-8")
    drift = export.drift(project)
    assert drift and drift[0]["page_id"] == page.id

