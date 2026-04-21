"""Tests for V1.1 semantic recall."""

from __future__ import annotations

import pytest


class StubProvider:
    """Deterministic, no-ML embedding provider for tests.

    Produces a vector by hashing tokens — similar texts share vocab so
    share vector components, which is enough to verify ordering.
    """

    model = "stub-model-v1"
    dim = 16

    def embed(self, texts):
        vectors = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in text.lower().split():
                slot = hash(tok) % self.dim
                vec[slot] += 1.0
            vectors.append(vec)
        return vectors


@pytest.fixture
def semantic_env(hippo_env):
    """Add a stub embedding provider on top of the base hippo_env fixture."""
    from hippocampus import embeddings

    embeddings.reset_provider()
    embeddings.set_provider(StubProvider())
    yield hippo_env
    embeddings.reset_provider()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_pack_unpack_roundtrip(hippo_env):
    from hippocampus.embeddings import store

    v = [0.1, -0.2, 3.1415, 0.0, 100.0]
    blob = store.pack(v)
    back = store.unpack(blob, dim=len(v))
    for a, b in zip(v, back):
        assert abs(a - b) < 1e-5


def test_store_crud(semantic_env):
    from hippocampus.embeddings import store
    from hippocampus.storage import fragments as F

    f = F.create("kafka consumers need idempotency", summary="kafka")
    store.put(f.id, [1.0, 0.0, 0.0], model="stub-model-v1")
    got = store.get(f.id)
    assert got is not None
    vec, model = got
    assert vec[0] == 1.0
    assert model == "stub-model-v1"

    assert store.count() == 1
    assert store.missing_ids() == []
    store.delete(f.id)
    assert store.count() == 0


# ---------------------------------------------------------------------------
# Reindex
# ---------------------------------------------------------------------------


def test_reindex_fills_missing(semantic_env):
    from hippocampus.embeddings import search, store
    from hippocampus.storage import fragments as F

    F.create("fragment one", summary="one")
    F.create("fragment two", summary="two")
    F.create("fragment three", summary="three")
    # Clear any embeddings put by remember() hook (fragments.create is direct)
    # — F.create is lower level and doesn't call remember(), so nothing embedded yet.

    result = search.reindex()
    assert result["status"] == "ok"
    assert result["embedded"] == 3
    assert store.count() == 3


def test_reindex_noop_when_provider_missing(hippo_env):
    from hippocampus.embeddings import search, reset_provider

    reset_provider()
    # No set_provider call -> load_provider will try fastembed, which may or
    # may not be installed. We force-disable by monkeypatching load_provider.
    import hippocampus.embeddings as E
    E._provider_singleton = None
    E._provider_available = False

    result = search.reindex()
    assert result["status"] == "unavailable"


# ---------------------------------------------------------------------------
# Hybrid recall
# ---------------------------------------------------------------------------


def test_cosine_math(hippo_env):
    from hippocampus.embeddings.search import cosine

    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([1.0, 1.0], [1.0, 1.0]) == pytest.approx(1.0)
    # Zero vector -> 0, not NaN
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_hybrid_recall_uses_semantic_when_fts_fails(semantic_env, monkeypatch):
    from hippocampus.embeddings import search as semantic_search
    from hippocampus.mcp import tools
    from hippocampus.storage import fragments as F

    # Store two fragments; search a query that doesn't share tokens with either
    F.create("idempotent kafka consumers avoid retries", summary="idempotency")
    F.create("completely unrelated note about weather", summary="weather")
    semantic_search.reindex()

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "pytest")
    out = tools.recall(query="idempotent kafka consumers", limit=2)
    assert out["count"] >= 1
    # The most semantically relevant hit should be the idempotency fragment
    assert "idempotency" in out["fragments"][0]["summary"]


def test_recall_falls_back_to_fts_without_provider(hippo_env, monkeypatch):
    """When no embedding provider is configured, recall still works (FTS only)."""
    import hippocampus.embeddings as E
    E._provider_singleton = None
    E._provider_available = False

    from hippocampus.mcp import tools
    from hippocampus.storage import fragments as F

    F.create("alpha beta gamma", summary="abc")
    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "pytest")
    out = tools.recall(query="alpha")
    assert out["count"] == 1
    assert out["semantic_available"] is False
    assert out["semantic_weight"] == 0.0
