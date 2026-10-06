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
from hippocampus.mcp import tools
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
    # Hydrate live fragments once so we can pick keepers in O(n)
    live = {f.id: f for f in frag_store.list_all(limit=10_000_000) if not f.superseded_by}
    vectors: list[tuple[str, list[float]]] = [
        (fid, vec) for fid, vec, _model in vstore.iter_all() if fid in live
    ]
    n = len(vectors)
    if n < 2:
        return []

    for i in range(n):
        fid_a, vec_a = vectors[i]
        for j in range(i + 1, n):
            fid_b, vec_b = vectors[j]
            if live[fid_a].project != live[fid_b].project:
                continue
            score = semantic_search.cosine(vec_a, vec_b)
            if score >= thr:
                conf_a = live[fid_a].confidence
                conf_b = live[fid_b].confidence
                if conf_a >= conf_b:
                    pairs.append(DuplicatePair(keeper=fid_a, loser=fid_b, score=score))
                else:
                    pairs.append(DuplicatePair(keeper=fid_b, loser=fid_a, score=score))

    pairs.sort(key=lambda p: -p.score)
    if limit and limit > 0:
        pairs = pairs[:limit]
    return pairs


def merge(keeper_id: str, loser_id: str) -> dict | None:
    """Merge `loser_id` into `keeper_id` and supersede the loser.

    Returns a summary dict, or None if either is missing. A pair that cannot
    be merged yields {"merged": False, "reason": ...}.
    """
    keeper = frag_store.get(keeper_id)
    loser = frag_store.get(loser_id)
    if keeper is None or loser is None:
        return None
    if keeper_id == loser_id:
        return {"merged": False, "reason": "cannot merge a fragment into itself"}
    if keeper.superseded_by:
        return {"merged": False, "reason": f"{keeper_id} is itself superseded by {keeper.superseded_by}"}
    try:
        tools._check_supersedable(loser_id, keeper.project)
    except ValueError as e:
        return {"merged": False, "reason": str(e)}

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

    tools._link_supersede(loser_id, keeper_id)

    # Best-effort: re-embed the keeper with merged content; remove loser
    try:
        semantic_search.upsert_for_fragment(keeper_id)
    except Exception:
        pass
    try:
        vstore.delete(loser_id)
    except Exception:
        pass

    return {
        "merged": True,
        "keeper": keeper_id,
        "loser": loser_id,
        "new_confidence": new_conf,
        "added_tags": add_tags,
    }
