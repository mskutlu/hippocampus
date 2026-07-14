"""V9 W8 — dedup near-duplicate fragments."""

from __future__ import annotations


def test_dedup_finds_near_duplicates(hippo_env, monkeypatch):
    from hippocampus.embeddings import dedup, store
    from hippocampus.mcp import tools as T

    monkeypatch.setenv("HIPPO_DEDUP_COSINE_THRESHOLD", "0.90")

    a = T.remember(content="Kafka consumers must be idempotent to handle redelivery.",
                   summary="Kafka idempotent consumer")
    b = T.remember(content="Kafka consumers should be idempotent so duplicates are safe.",
                   summary="idempotent Kafka consumer")
    # A clearly-different fragment
    c = T.remember(content="The acme-orders service deploys via GitLab CI to Docker Hub.",
                   summary="acme-orders deploy")
    store.put(a["fragment"]["id"], [1.0, 0.0], model="stub")
    store.put(b["fragment"]["id"], [0.99, 0.01], model="stub")
    store.put(c["fragment"]["id"], [0.0, 1.0], model="stub")

    pairs = dedup.find_duplicates(threshold=0.90)
    pair_ids = {frozenset((p.keeper, p.loser)) for p in pairs}
    assert frozenset((a["fragment"]["id"], b["fragment"]["id"])) in pair_ids
    # The deploy fragment should NOT pair with the kafka ones
    for p in pairs:
        assert c["fragment"]["id"] not in {p.keeper, p.loser}


def test_dedup_returns_empty_when_corpus_unique(hippo_env, monkeypatch):
    from hippocampus.embeddings import dedup, store
    from hippocampus.mcp import tools as T

    monkeypatch.setenv("HIPPO_DEDUP_COSINE_THRESHOLD", "0.99")
    first = T.remember(content="Kafka consumers must be idempotent.")
    second = T.remember(content="The sky is blue.")
    third = T.remember(content="Compile-time errors are surfaced by typecheck.")
    store.put(first["fragment"]["id"], [1.0, 0.0, 0.0], model="stub")
    store.put(second["fragment"]["id"], [0.0, 1.0, 0.0], model="stub")
    store.put(third["fragment"]["id"], [0.0, 0.0, 1.0], model="stub")

    assert dedup.find_duplicates(threshold=0.99) == []


def test_dedup_merge_keeps_keeper_kills_loser(hippo_env, monkeypatch):
    from hippocampus.embeddings import dedup
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    a = T.remember(
        content="Kafka idempotency essentials.",
        summary="Kafka idempotency",
        tags=["kafka", "idempotent"],
    )
    b = T.remember(
        content="Kafka consumer idempotency notes (extra detail).",
        summary="Kafka consumer idempotency notes",
        tags=["kafka", "duplicate-protection"],
    )
    out = dedup.merge(a["fragment"]["id"], b["fragment"]["id"])
    assert out["merged"] is True

    kept = F.get(a["fragment"]["id"])
    assert kept is not None
    assert F.get(b["fragment"]["id"]) is None  # loser deleted
    # Loser tags merged in
    assert "duplicate-protection" in kept.tags
    # Loser content appended
    assert "extra detail" in (kept.content or "").lower()
