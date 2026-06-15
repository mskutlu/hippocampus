from __future__ import annotations


def test_wiki_lint_detects_broken_link_and_orphan(hippo_env):
    from hippocampus.wiki import lint, storage, workspace

    workspace.init_project(project="demo")
    project = storage.get_project_by_key("demo")
    assert project is not None
    storage.upsert_page(
        project.id,
        page_type="concept",
        title="Broken",
        markdown="# Broken\n\n[[missing/Page]]\n",
        frontmatter={"title": "Broken", "type": "concept", "status": "draft", "sources": [], "tags": []},
    )
    result = lint.run(project="demo")
    codes = {i["code"] for i in result["issues"]}
    assert "broken_wikilink" in codes
    assert "orphan_page" in codes
    assert "uncited_page" in codes

