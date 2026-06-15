from __future__ import annotations


def test_wiki_naming_helpers(hippo_env):
    from hippocampus.wiki import naming

    assert naming.normalize_title("Durable Memory!", "concept") == "concept:durable-memory"
    assert naming.filename_for_title("Durable Memory!") == "Durable-Memory.md"
    assert naming.path_for_page("concept", "Durable Memory") == "wiki/concepts/Durable-Memory.md"
    assert naming.path_for_page("index", "Anything") == "wiki/index.md"

