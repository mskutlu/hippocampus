from __future__ import annotations


def test_project_key_explicit_is_normalized(hippo_env):
    from hippocampus.wiki import projects

    assert projects.derive_project_key("My Project!") == "my-project"


def test_require_project_blocks_when_missing(hippo_env):
    from hippocampus.wiki import projects

    project, blocked = projects.require_project("missing")
    assert project is None
    assert blocked is not None
    assert blocked["blocked"] is True
    assert blocked["reason"] == "wiki_not_initialized"

