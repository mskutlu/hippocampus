"""V9 W4 — log_progress semantically recalls + cluster-boosts the knowledge graph."""

from __future__ import annotations


def test_log_progress_boosts_semantic_match(hippo_env, monkeypatch):
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    monkeypatch.setenv("HIPPO_LOG_PROGRESS_RECALL_BOOST_K", "3")
    monkeypatch.setenv("HIPPO_LOG_PROGRESS_RECALL_MIN_SCORE", "0.30")

    rmem = T.remember(
        content="Kafka consumers must be idempotent to handle redelivery.",
        summary="Kafka consumer idempotency",
    )
    fid = rmem["fragment"]["id"]
    before = F.get(fid).confidence

    out = T.log_progress(
        kind="done",
        content="Refactored the order-consumer to be idempotent across retries.",
    )
    assert out["logged"] is True

    after = F.get(fid).confidence
    assert after > before, "Semantic-match fragment should have been auto-boosted"
    assert fid in out.get("auto_boosted_fragments", [])


def test_log_progress_no_explicit_id_still_boosts(hippo_env, monkeypatch):
    """Even with no `frag_…` reference, semantic match alone should boost."""
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    monkeypatch.setenv("HIPPO_LOG_PROGRESS_RECALL_BOOST_K", "3")
    monkeypatch.setenv("HIPPO_LOG_PROGRESS_RECALL_MIN_SCORE", "0.30")

    rmem = T.remember(content="The acme-orders service deploys via GitLab CI to Docker Hub.")
    fid = rmem["fragment"]["id"]
    before = F.get(fid).confidence

    out = T.log_progress(kind="ask", content="How does acme-orders deploy to Docker Hub?")
    assert fid in out.get("auto_boosted_fragments", [])
    assert F.get(fid).confidence > before


def test_log_progress_respects_disable(hippo_env, monkeypatch):
    from hippocampus.mcp import tools as T

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    monkeypatch.setenv("HIPPO_LOG_PROGRESS_RECALL_BOOST_K", "0")

    T.remember(content="Kafka consumers must be idempotent.")
    out = T.log_progress(kind="done", content="Fixed idempotent consumer behaviour.")
    assert out.get("auto_boosted_fragments", []) == []
