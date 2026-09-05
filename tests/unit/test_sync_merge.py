"""V11 Phase 3 — pure merge rules."""

from __future__ import annotations

from hippocampus.sync import merge


def _frag(**kw):
    base = {
        "id": "frag_a", "content": "c", "summary": "s", "source_type": "manual", "source_ref": None,
        "confidence": 0.5, "accessed": 0, "last_accessed_at": None, "created_at": "2026-01-01T00:00:00.000Z",
        "updated_at": "2026-01-01T00:00:00.000Z", "pinned": 0, "below_threshold_since": None, "project": None,
        "tags": [],
    }
    base.update(kw)
    return base


def test_no_local_takes_remote():
    r = _frag(tags=["b", "a", "a"])
    out = merge.merge_fragment(None, r)
    assert out["content"] == "c" and out["tags"] == ["a", "b"]
    assert merge.changed(None, out)


def test_newer_content_wins_and_dynamics_take_max():
    local = _frag(content="old", updated_at="2026-01-02T00:00:00.000Z", accessed=10, confidence=0.9,
                  last_accessed_at="2026-01-05T00:00:00.000Z", pinned=1)
    remote = _frag(content="new", updated_at="2026-01-03T00:00:00.000Z", accessed=3, confidence=0.6,
                   last_accessed_at="2026-01-02T00:00:00.000Z", pinned=0, project="acme")
    out = merge.merge_fragment(local, remote)
    assert out["content"] == "new" and out["project"] == "acme"
    assert out["pinned"] == 0  # the newer edit carries the pin decision (an unpin)
    assert out["accessed"] == 10 and out["confidence"] == 0.9
    assert out["last_accessed_at"] == "2026-01-05T00:00:00.000Z"
    assert out["updated_at"] == "2026-01-03T00:00:00.000Z"


def test_older_remote_only_contributes_dynamics():
    local = _frag(content="mine", updated_at="2026-01-05T00:00:00.000Z", accessed=1, tags=["x"])
    remote = _frag(content="theirs", updated_at="2026-01-01T00:00:00.000Z", accessed=7, tags=["y"])
    out = merge.merge_fragment(local, remote)
    assert out["content"] == "mine" and out["accessed"] == 7 and out["tags"] == ["x", "y"]


def test_merge_is_commutative_on_dynamics_and_content():
    a = _frag(content="A", updated_at="2026-01-02T00:00:00.000Z", accessed=2, confidence=0.4, tags=["a"])
    b = _frag(content="B", updated_at="2026-01-03T00:00:00.000Z", accessed=5, confidence=0.7, tags=["b"])
    ab, ba = merge.merge_fragment(a, b), merge.merge_fragment(b, a)
    for k in ("content", "accessed", "confidence", "updated_at", "tags", "created_at"):
        assert ab[k] == ba[k]


def test_changed_false_when_identical():
    local = _frag(tags=["a"])
    out = merge.merge_fragment(local, _frag(tags=["a"]))
    assert not merge.changed(local, out)


def test_below_threshold_cleared_when_confidence_recovers():
    local = _frag(confidence=0.01, below_threshold_since="2026-01-01T00:00:00.000Z")
    remote = _frag(confidence=0.3)
    assert merge.merge_fragment(local, remote)["below_threshold_since"] is None
    low = merge.merge_fragment(local, _frag(confidence=0.02, below_threshold_since="2025-12-01T00:00:00.000Z"))
    assert low["below_threshold_since"] == "2025-12-01T00:00:00.000Z"


def test_tombstone_rules():
    assert merge.tombstone_wins(None, "2026-01-01T00:00:00.000Z") is False
    local = _frag(updated_at="2026-01-02T00:00:00.000Z")
    assert merge.tombstone_wins(local, "2026-01-03T00:00:00.000Z") is True
    assert merge.tombstone_wins(local, "2026-01-01T00:00:00.000Z") is False


def test_association_merge():
    a = {"fragment_a": "x", "fragment_b": "y", "weight": 2.0, "co_accessed_count": 2, "last_co_accessed_at": "2026-01-01T00:00:00.000Z"}
    b = {"fragment_a": "x", "fragment_b": "y", "weight": 1.0, "co_accessed_count": 5, "last_co_accessed_at": "2026-02-01T00:00:00.000Z"}
    out = merge.merge_association(a, b)
    assert out == {"fragment_a": "x", "fragment_b": "y", "weight": 2.0, "co_accessed_count": 5, "last_co_accessed_at": "2026-02-01T00:00:00.000Z"}
