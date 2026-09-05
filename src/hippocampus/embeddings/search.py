"""Cosine-similarity search + upsert helpers.

Ranking uses one numpy matrix multiply over a normalized, in-process cached
matrix (numpy ships with every embedding provider). The pure-Python path is
kept only as a fallback when numpy is unavailable.
"""

from __future__ import annotations

import logging
import math
from typing import Iterable

from hippocampus import config
from hippocampus.embeddings import load_provider, store as vstore
from hippocampus.storage import fragments as frag_store

log = logging.getLogger("hippocampus.embeddings.search")


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = _norm(a), _norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return _dot(a, b) / (na * nb)


def _text_for_fragment(frag) -> str:
    """The text we embed for a fragment: summary followed by content."""
    summary = (frag.summary or "").strip()
    content = (frag.content or "").strip()
    if summary and content and summary.lower() not in content.lower():
        return f"{summary}\n\n{content}"
    return content or summary


def upsert_for_fragment(fragment_id: str) -> bool:
    """Embed one fragment and store its vector. Returns True on success.

    Never raises: returns False on any error (logged to stderr).
    """
    provider = load_provider()
    if provider is None:
        return False
    frag = frag_store.get(fragment_id)
    if frag is None:
        return False
    text = _text_for_fragment(frag)
    if not text:
        return False
    try:
        vectors = provider.embed([text])
        vstore.put(fragment_id, vectors[0], model=provider.model)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("embedding failed for fragment %s: %s", fragment_id, e)
        return False


def reindex(force: bool = False, batch: int = 64) -> dict:
    """Embed missing (or all if force) fragments. Returns a stats dict."""
    provider = load_provider()
    if provider is None:
        return {"status": "unavailable", "reason": "provider_not_loaded"}

    model = provider.model
    target_ids: list[str] = (
        [f.id for f in frag_store.list_all(limit=10_000_000)]
        if force
        else vstore.missing_ids(model=model)
    )
    total = len(target_ids)
    embedded = 0
    errors = 0

    for i in range(0, total, batch):
        chunk_ids = target_ids[i : i + batch]
        # Gather texts, skipping any that disappear mid-flight.
        frags = [frag_store.get(fid) for fid in chunk_ids]
        pairs = [(f.id, _text_for_fragment(f)) for f in frags if f is not None]
        pairs = [(fid, t) for fid, t in pairs if t]
        if not pairs:
            continue
        try:
            vectors = provider.embed([t for _, t in pairs])
        except Exception as e:  # noqa: BLE001
            log.warning("batch embed failed: %s", e)
            errors += len(pairs)
            continue
        for (fid, _), vec in zip(pairs, vectors):
            try:
                vstore.put(fid, vec, model=model)
                embedded += 1
            except Exception as e:  # noqa: BLE001
                log.warning("store failed for %s: %s", fid, e)
                errors += 1

    return {
        "status": "ok",
        "total_fragments_targeted": total,
        "embedded": embedded,
        "errors": errors,
        "model": model,
        "dim": provider.dim,
    }


_MATRIX_CACHE: dict[str, tuple[tuple[int, str], list[str], "object"]] = {}


def _matrix_for(model: str):
    """(ids, normalized float32 matrix) for `model`, cached per process.

    The cache key is (row count, newest created_at) so any put/delete since
    the last call rebuilds it; the check is one cheap aggregate query.
    """
    import numpy as np

    from hippocampus.storage.db import get_ro_conn

    with get_ro_conn() as conn:
        sig_row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(MAX(created_at), '') AS t FROM fragment_embeddings WHERE model = ?",
            (model,),
        ).fetchone()
        sig = (int(sig_row["n"]), str(sig_row["t"]))
        cached = _MATRIX_CACHE.get(model)
        if cached and cached[0] == sig:
            return cached[1], cached[2]
        rows = conn.execute(
            "SELECT fragment_id, vector, dim FROM fragment_embeddings WHERE model = ?", (model,)
        ).fetchall()
    if not rows:
        _MATRIX_CACHE[model] = (sig, [], None)
        return [], None
    dim = int(rows[0]["dim"])
    ids = [r["fragment_id"] for r in rows if int(r["dim"]) == dim]
    buf = b"".join(bytes(r["vector"]) for r in rows if int(r["dim"]) == dim)
    mat = np.frombuffer(buf, dtype=np.float32).reshape(len(ids), dim).astype(np.float32, copy=True)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat /= norms
    _MATRIX_CACHE[model] = (sig, ids, mat)
    return ids, mat


def _topk_numpy(q_vec: list[float], model: str, k: int, allowed_ids: set[str] | None) -> list[tuple[str, float]]:
    import numpy as np

    ids, mat = _matrix_for(model)
    if mat is None or not ids:
        return []
    q = np.asarray(q_vec, dtype=np.float32)
    qn = float(np.linalg.norm(q))
    if qn == 0:
        return []
    scores = mat @ (q / qn)
    if allowed_ids is not None:
        mask = np.fromiter((fid in allowed_ids for fid in ids), dtype=bool, count=len(ids))
        scores = np.where(mask, scores, -np.inf)
    n = min(k, len(ids))
    if n <= 0:
        return []
    top = np.argpartition(-scores, n - 1)[:n]
    top = top[np.argsort(-scores[top])]
    return [(ids[i], float(scores[i])) for i in top if np.isfinite(scores[i])]


def semantic_topk(
    query: str,
    k: int = 5,
    *,
    allowed_ids: set[str] | None = None,
) -> list[tuple[str, float]]:
    """Return [(fragment_id, cosine_score)] for top-k semantic matches.

    `allowed_ids` restricts candidates before ranking (project scope, V11) so
    hidden fragments never consume the k slots. Empty list if embeddings
    aren't loaded or the provider is unavailable.
    """
    provider = load_provider()
    if provider is None:
        return []
    if vstore.count() == 0:
        return []
    try:
        q_vec = provider.embed([query])[0]
    except Exception as e:  # noqa: BLE001
        log.warning("query embed failed: %s", e)
        return []

    try:
        return _topk_numpy(q_vec, provider.model, k, allowed_ids)
    except ImportError:
        pass

    results: list[tuple[str, float]] = []
    for fid, vec, _ in vstore.iter_all(model=provider.model):
        if allowed_ids is not None and fid not in allowed_ids:
            continue
        results.append((fid, cosine(q_vec, vec)))
    results.sort(key=lambda t: -t[1])
    return results[:k]


def stats() -> dict:
    provider = load_provider()
    embedded = vstore.count()
    total = frag_store.count()
    configured_weight = config.get_setting("semantic_weight")
    return {
        "embedded": embedded,
        "total_fragments": total,
        "coverage": round(embedded / total, 4) if total else 0.0,
        "model": provider.model if provider else None,
        "dim": provider.dim if provider else None,
        "provider_available": provider is not None,
        "semantic_weight": 0.5 if configured_weight is None else configured_weight,
    }
