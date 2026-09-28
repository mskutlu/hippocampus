"""remember() surfaces same-project near-duplicates as `similar`."""

from __future__ import annotations

import pytest

from tests.integration.test_semantic_recall import StubProvider

DUPLICATE = "kafka consumers must be idempotent on every retry"


@pytest.fixture
def semantic_env(hippo_env):
    from hippocampus import embeddings

    embeddings.reset_provider()
    embeddings.set_provider(StubProvider())
    yield hippo_env
    embeddings.reset_provider()


def test_remember_reports_similar_in_same_project_only(semantic_env):
    from hippocampus.mcp import tools

    original = tools.remember(DUPLICATE, project="alpha")["fragment"]
    tools.remember(DUPLICATE, project="beta")
    before = tools.get_fragment(original["id"], boost_on_read=False)["fragment"]["confidence"]

    out = tools.remember(DUPLICATE + " attempt", project="alpha")

    assert [s["id"] for s in out["similar"]] == [original["id"]]
    assert out["similar"][0]["score"] >= 0.85
    assert original["id"] in out["hint"]
    after = tools.get_fragment(original["id"], boost_on_read=False)["fragment"]["confidence"]
    assert after == before


def test_remember_skips_similar_when_superseding_or_no_embeddings(semantic_env):
    from hippocampus import embeddings
    from hippocampus.mcp import tools

    original = tools.remember(DUPLICATE, project="alpha")["fragment"]
    out = tools.remember(DUPLICATE + " attempt", project="alpha", supersedes=original["id"])
    assert "similar" not in out

    embeddings.set_provider(None)
    out = tools.remember(DUPLICATE + " again", project="alpha")
    assert "similar" not in out

