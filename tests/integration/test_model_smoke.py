from __future__ import annotations

import os

import pytest


@pytest.mark.model_smoke
@pytest.mark.skipif(
    os.environ.get("HIPPO_RUN_MODEL_SMOKE") != "1",
    reason="set HIPPO_RUN_MODEL_SMOKE=1",
)
def test_real_fastembed_provider(hippo_env, monkeypatch):
    from hippocampus import embeddings

    monkeypatch.setenv("HIPPO_EMBEDDING_PROVIDER", "fastembed")
    embeddings.reset_provider()
    provider = embeddings.load_provider()

    assert provider is not None
    assert len(provider.embed(["model smoke"])[0]) == provider.dim
