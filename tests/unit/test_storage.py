"""Unit tests for storage.fragments + FTS + associations."""

from __future__ import annotations


def test_create_get_round_trip(hippo_env):
    from hippocampus.storage import fragments as F

    f = F.create("content body", summary="summary", tags=["a", "b"])
    got = F.get(f.id)
    assert got is not None
    assert got.content == "content body"
    assert got.summary == "summary"
    assert set(got.tags) == {"a", "b"}


def test_fts_search(hippo_env):
    from hippocampus.storage import fragments as F

    F.create("kafka retries idempotent", summary="k")
    F.create("redis lock timeout", summary="r")
    hits = F.search_fts("kafka")
    assert len(hits) == 1
    assert "kafka" in hits[0].content


def test_fts_respects_min_confidence(hippo_env):
    from hippocampus.storage import fragments as F

    f = F.create("alpha beta gamma", summary="a")
    F.update_fields(f.id, confidence=0.2)
    assert F.search_fts("alpha", min_confidence=0.5) == []
    assert len(F.search_fts("alpha", min_confidence=0.1)) == 1


def test_update_fields_saturates_confidence(hippo_env):
    from hippocampus.storage import fragments as F

    f = F.create("c", summary="s")
    F.update_fields(f.id, confidence=2.0)  # should clamp to 1.0
    assert F.get(f.id).confidence == 1.0
    F.update_fields(f.id, confidence=-1.0)  # should clamp to 0.0
    assert F.get(f.id).confidence == 0.0


def test_associations_canonical_and_weighted(hippo_env):
    from hippocampus.storage import associations, fragments as F

    a = F.create("a", summary="a")
    b = F.create("b", summary="b")
    # Call in both orders — should collapse to a single edge
    associations.strengthen(a.id, b.id)
    associations.strengthen(b.id, a.id)
    associations.strengthen(a.id, b.id)

    edges = associations.get_associated(a.id)
    assert len(edges) == 1
    other, weight, count = edges[0]
    assert other == b.id
    assert weight > 2.0
    assert count == 3


def test_delete_removes_from_db_and_mirror(hippo_env):
    from hippocampus.storage import fragments as F

    f = F.create("c", summary="s")
    path = hippo_env["fragments_dir"] / f"{f.id}.md"
    assert path.exists()
    assert F.delete(f.id) is True
    assert F.get(f.id) is None
    assert not path.exists()
