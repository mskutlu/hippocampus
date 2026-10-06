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


def test_dedup_merge_supersedes_loser(hippo_env, monkeypatch):
    from hippocampus.embeddings import dedup, store
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
    a_id, b_id = a["fragment"]["id"], b["fragment"]["id"]
    store.put(a_id, [1.0, 0.0], model="stub")
    store.put(b_id, [0.99, 0.01], model="stub")

    out = dedup.merge(a_id, b_id)
    assert out["merged"] is True

    kept = F.get(a_id)
    loser = F.get(b_id)
    assert kept is not None and kept.superseded_by is None
    assert loser is not None and loser.superseded_by == a_id
    assert store.get(b_id) is None
    assert "duplicate-protection" in kept.tags
    assert "extra detail" in (kept.content or "").lower()
    recalled = {f["id"] for f in T.recall("Kafka consumer idempotency notes")["fragments"]}
    assert a_id in recalled and b_id not in recalled


def test_dedup_skips_cross_project_pairs(hippo_env):
    from hippocampus.embeddings import dedup, store
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    a = T.remember(content="Kafka consumers must be idempotent.", project="alpha")["fragment"]["id"]
    b = T.remember(content="Kafka consumers should be idempotent.", project="beta")["fragment"]["id"]
    store.put(a, [1.0, 0.0], model="stub")
    store.put(b, [0.99, 0.01], model="stub")

    assert dedup.find_duplicates(threshold=0.90) == []
    out = dedup.merge(a, b)
    assert out["merged"] is False
    assert "beta" in out["reason"]
    assert F.get(b).superseded_by is None
    assert F.get(a).content == "Kafka consumers must be idempotent."


def test_dedup_skips_superseded_fragments(hippo_env):
    from hippocampus.embeddings import dedup, store
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    old = T.remember(content="Kafka consumers must be idempotent.")["fragment"]["id"]
    dup = T.remember(content="Kafka consumers should be idempotent.")["fragment"]["id"]
    new = T.remember(content="Kafka consumers need idempotent handlers.")["fragment"]["id"]
    for fid, vec in ((old, [1.0, 0.0]), (dup, [0.99, 0.01]), (new, [0.98, 0.02])):
        store.put(fid, vec, model="stub")
    T.supersede(old, new)

    pair_ids = {frozenset((p.keeper, p.loser)) for p in dedup.find_duplicates(threshold=0.90)}
    assert pair_ids == {frozenset((dup, new))}

    assert dedup.merge(new, old)["merged"] is False
    assert dedup.merge(old, dup)["merged"] is False
    assert F.get(dup).superseded_by is None
