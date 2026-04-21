"""Tests for V1.3: sentence-transformers provider + bench tool."""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


def test_load_provider_respects_env(hippo_env, monkeypatch):
    from hippocampus import embeddings

    embeddings.reset_provider()
    monkeypatch.setenv("HIPPO_EMBEDDING_PROVIDER", "unknown-backend-xyz")
    p = embeddings.load_provider()
    assert p is None


def test_load_provider_falls_back_gracefully(hippo_env, monkeypatch):
    """If sentence-transformers isn't installed we should warn, not crash."""
    from hippocampus import embeddings

    embeddings.reset_provider()
    monkeypatch.setenv("HIPPO_EMBEDDING_PROVIDER", "sentence-transformers")
    monkeypatch.setenv("HIPPO_EMBEDDING_MODEL", "this/model-does-not-exist")
    # Sentence-transformers IS installed in this env, but the model name is
    # bogus — provider load should return None (handled via except Exception).
    p = embeddings.load_provider()
    assert p is None


# ---------------------------------------------------------------------------
# Bench tool (stub provider)
# ---------------------------------------------------------------------------


class StubProvider:
    """Deterministic provider for bench tests — no network."""

    model = "stub-bench-v1"
    dim = 4
    device = "cpu"

    def embed(self, texts):
        # Trivial hashed vectors to get deterministic, non-zero cosines.
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for i, ch in enumerate(t.lower()):
                v[i % self.dim] += (ord(ch) % 13) * 0.01
            out.append(v)
        return out


@pytest.fixture
def stub_env(hippo_env, monkeypatch):
    from hippocampus import embeddings
    from hippocampus.storage import fragments as F

    embeddings.reset_provider()
    embeddings.set_provider(StubProvider())

    F.create("alpha content", summary="alpha")
    F.create("beta content",  summary="beta")
    F.create("gamma content", summary="gamma")
    return hippo_env


def test_bench_self_retrieval_all_hits(stub_env, monkeypatch):
    from hippocampus.embeddings import bench

    # Force the bench to use our injected stub provider by making
    # load_provider return it no matter what model name comes through.
    from hippocampus import embeddings

    def _always_stub():
        embeddings._provider_singleton = StubProvider()
        embeddings._provider_available = True
        return embeddings._provider_singleton
    monkeypatch.setattr(embeddings, "load_provider", _always_stub)

    result = bench.bench(models=["stub-a", "stub-b"], provider="sentence-transformers")
    assert result["queries_tested"] == 3
    assert len(result["models"]) == 2
    for row in result["models"]:
        # With only 3 fragments, every expected id should be in top-5.
        assert row["hit@5"] == "100.0%"
        assert row["errors"] == 0


def test_bench_empty_store(hippo_env, monkeypatch):
    """Bench must not crash when there are zero fragments — returns a friendly error."""
    from hippocampus.embeddings import bench

    result = bench.bench(models=["anything"], provider="fastembed")
    assert "error" in result  # no queries available
