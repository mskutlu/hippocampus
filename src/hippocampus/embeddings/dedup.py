"""Near-duplicate fragment detection.

For a personal-scale corpus (<10k fragments) a pairwise O(n²) cosine scan
finishes in well under a second. Pairs above the configured threshold are
candidates for merging via `hippo dedup --merge a b`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from hippocampus import config
from hippocampus.embeddings import search as semantic_search
from hippocampus.embeddings import store as vstore
from hippocampus.storage import fragments as frag_store


@dataclass
class DuplicatePair:
    keeper: str          # higher-confidence fragment id
    loser: str           # lower-confidence fragment id
    score: float         # cosine similarity in [0, 1]


def find_duplicates(*, threshold: float | None = None, limit: int | None = None) -> list[DuplicatePair]:
    """Pairwise-scan all stored embeddings and return pairs above threshold."""
    thr = float(threshold if threshold is not None else config.get_setting("dedup_cosine_threshold") or 0.95)

    pairs: list[DuplicatePair] = []
    vectors: list[tuple[str, list[float]]] = [
        (fid, vec) for fid, vec, _model in vstore.iter_all()
    ]
    n = len(vectors)
    if n < 2:
        return []

    # Hydrate confidence once so we can pick keepers in O(n)
    confidences: dict[str, float] = {}
    for f in frag_store.list_all(limit=10_000_000):
        confidences[f.id] = f.confidence

    for i in range(n):
        fid_a, vec_a = vectors[i]
        for j in range(i + 1, n):
            fid_b, vec_b = vectors[j]
            score = semantic_search.cosine(vec_a, vec_b)
            if score >= thr:
                conf_a = confidences.get(fid_a, 0.0)
                conf_b = confidences.get(fid_b, 0.0)
                if conf_a >= conf_b:
                    pairs.append(DuplicatePair(keeper=fid_a, loser=fid_b, score=score))
                else:
                    pairs.append(DuplicatePair(keeper=fid_b, loser=fid_a, score=score))

    pairs.sort(key=lambda p: -p.score)
    if limit and limit > 0:
        pairs = pairs[:limit]
    return pairs


def merge(keeper_id: str, loser_id: str) -> dict | None:
    """Merge `loser_id` into `keeper_id`. Returns a summary dict, or None if either is missing."""
    keeper = frag_store.get(keeper_id)
    loser = frag_store.get(loser_id)
    if keeper is None or loser is None:
        return None

    # Copy loser's tags to keeper (canonicalization handled in update_fields)
    add_tags = [t for t in (loser.tags or []) if t not in (keeper.tags or [])]
    # Append loser content if it's not already a substring of the keeper
    new_content = keeper.content or ""
    loser_content = (loser.content or "").strip()
    if loser_content and loser_content.lower() not in new_content.lower():
        new_content = f"{new_content}\n\n---\n[merged from {loser_id}]\n{loser_content}".strip()

    new_conf = max(keeper.confidence, loser.confidence)
    new_accessed = (keeper.accessed or 0) + (loser.accessed or 0)
    pinned = keeper.pinned or loser.pinned

    frag_store.update_fields(
        keeper_id,
        content=new_content,
        confidence=new_conf,
        pinned=pinned,
        add_tags=add_tags,
    )
    # Override accessed counter directly — update_fields supports `accessed_delta`,
    # so we apply the delta needed to reach the merged total.
    delta = new_accessed - (keeper.accessed or 0)
    if delta > 0:
        frag_store.update_fields(keeper_id, accessed_delta=int(delta))

    # Best-effort: re-embed the keeper with merged content; remove loser
    try:
        semantic_search.upsert_for_fragment(keeper_id)
    except Exception:
        pass
    try:
        vstore.delete(loser_id)
    except Exception:
        pass
    try:
        frag_store.delete(loser_id)
    except Exception:
        pass

    return {
        "merged": True,
        "keeper": keeper_id,
        "loser": loser_id,
        "new_confidence": new_conf,
        "added_tags": add_tags,
    }
