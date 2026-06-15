from __future__ import annotations


def test_frontmatter_roundtrip(hippo_env):
    from hippocampus.wiki import frontmatter

    text = frontmatter.render_frontmatter({"title": "A", "tags": ["x"]}, "# Body")
    meta, body = frontmatter.split_frontmatter(text)
    assert meta["title"] == "A"
    assert meta["tags"] == ["x"]
    assert body.strip() == "# Body"


def test_frontmatter_missing_is_safe(hippo_env):
    from hippocampus.wiki import frontmatter

    meta, body = frontmatter.split_frontmatter("# Body")
    assert meta == {}
    assert body == "# Body"

