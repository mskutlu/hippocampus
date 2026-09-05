"""V9 W5 — tag canonicalization."""

from __future__ import annotations


def test_canonicalize_matches_existing_by_ratio(hippo_env, monkeypatch):
    from hippocampus.mcp import tools as T
    from hippocampus.storage import tag_canonical

    monkeypatch.setenv("HIPPO_TAG_CANONICALIZE_THRESHOLD", "0.85")

    # Seed an existing canonical tag
    T.remember(content="seed", summary="seed", tags=["warehouse"])

    # Near-miss variants should fold onto the canonical
    assert tag_canonical.canonicalize_one("Warehouse") == "warehouse"
    assert tag_canonical.canonicalize_one("warehouses") == "warehouse"
    assert tag_canonical.canonicalize_one("ware-house") == "warehouse"

    # Truly different tag is preserved
    assert tag_canonical.canonicalize_one("acme-orders") == "acme-orders"


def test_canonicalize_disabled_at_zero(hippo_env, monkeypatch):
    from hippocampus.mcp import tools as T
    from hippocampus.storage import tag_canonical

    monkeypatch.setenv("HIPPO_TAG_CANONICALIZE_THRESHOLD", "0")
    T.remember(content="seed", summary="seed", tags=["warehouse"])
    assert tag_canonical.canonicalize_one("WAREHOUSES") == "WAREHOUSES"


def test_create_uses_canonical_form(hippo_env, monkeypatch):
    from hippocampus.mcp import tools as T

    monkeypatch.setenv("HIPPO_TAG_CANONICALIZE_THRESHOLD", "0.85")

    first = T.remember(content="A", summary="A", tags=["debugging"])
    second = T.remember(content="B", summary="B", tags=["DEBUGGING"])
    # Second insert should pick up the canonical "debugging" from the first.
    assert "debugging" in second["fragment"]["tags"]


def test_canonicalize_dedupes_within_input(hippo_env, monkeypatch):
    from hippocampus.storage import tag_canonical

    monkeypatch.setenv("HIPPO_TAG_CANONICALIZE_THRESHOLD", "0.85")
    out = tag_canonical.canonicalize(["foo", "Foo", "FOO"])
    assert out == ["foo"]
