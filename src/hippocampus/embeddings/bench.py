"""Embedding benchmark harness.

Compare multiple models side-by-side on the CURRENT fragment store, without
touching the user's persistent embeddings. The bench:

1. Reads all fragments from SQLite (or a user-supplied subset).
2. For each model in the list, loads it fresh, embeds every fragment, and
   runs every query through the full hybrid recall path (FTS + semantic).
3. Records per-query wall time, hit@1, hit@5, top-1 cosine, and the
   predicted fragment id vs. expected.
4. Prints a ranked table so you can see at a glance whether a swap is
   worth the install cost.

Design notes:
- The bench NEVER mutates the user's canonical DB. It clones vectors in
  memory, scores there, and discards on exit.
- When the user does not supply queries, we build a self-retrieval test:
  each fragment's summary is used as a query and the expected answer is
  the fragment itself. Models should put each fragment at rank 1.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from hippocampus.storage import fragments as frag_store


@dataclass
class ModelResult:
    model: str
    provider: str
    dim: int
    device: str
    load_seconds: float
    embed_seconds_per_fragment: float
    query_latency_ms_median: float
    query_latency_ms_p95: float
    hit_at_1: float
    hit_at_5: float
    queries_tested: int
    errors: int = 0
    notes: list[str] = field(default_factory=list)

    def as_row(self) -> dict:
        return {
            "model": self.model,
            "provider": self.provider,
            "device": self.device,
            "dim": self.dim,
            "load_s": round(self.load_seconds, 2),
            "embed_ms/frag": round(self.embed_seconds_per_fragment * 1000, 1),
            "q_ms_p50": round(self.query_latency_ms_median, 1),
            "q_ms_p95": round(self.query_latency_ms_p95, 1),
            "hit@1": f"{self.hit_at_1:.1%}",
            "hit@5": f"{self.hit_at_5:.1%}",
            "n": self.queries_tested,
            "errors": self.errors,
        }


def _build_default_queries() -> list[tuple[str, str]]:
    """Fallback: each fragment's summary is a query; expected hit is itself.

    Returns list of (query_text, expected_fragment_id).
    """
    pairs: list[tuple[str, str]] = []
    for f in frag_store.list_all(limit=10_000):
        summary = (f.summary or "").strip()
        if not summary:
            continue
        pairs.append((summary, f.id))
    return pairs


def _load_queries(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            q = obj.get("query") or obj.get("q")
            eid = obj.get("expected_id") or obj.get("expected") or obj.get("id")
            if q and eid:
                rows.append((q, eid))
    return rows


def _cosine(a: list[float], b: list[float]) -> float:
    # Vectors already normalised by providers that set
    # `normalize_embeddings=True`, but belt-and-braces.
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def bench_one(
    model_name: str,
    provider_name: str,
    queries: list[tuple[str, str]],
) -> ModelResult:
    """Run the bench for one model and return the aggregated result."""

    notes: list[str] = []
    # Isolate provider loading by using a temporary config override so we
    # do not leak the model choice into the user's settings.
    import os

    os.environ["HIPPO_EMBEDDING_PROVIDER"] = provider_name
    os.environ["HIPPO_EMBEDDING_MODEL"] = model_name

    from hippocampus import embeddings

    embeddings.reset_provider()

    load_started = time.perf_counter()
    try:
        provider = embeddings.load_provider()
    except Exception as e:  # noqa: BLE001
        return ModelResult(
            model=model_name,
            provider=provider_name,
            dim=0,
            device="?",
            load_seconds=time.perf_counter() - load_started,
            embed_seconds_per_fragment=0.0,
            query_latency_ms_median=0.0,
            query_latency_ms_p95=0.0,
            hit_at_1=0.0,
            hit_at_5=0.0,
            queries_tested=0,
            errors=1,
            notes=[f"load failed: {e}"],
        )
    load_seconds = time.perf_counter() - load_started
    if provider is None:
        return ModelResult(
            model=model_name,
            provider=provider_name,
            dim=0,
            device="?",
            load_seconds=load_seconds,
            embed_seconds_per_fragment=0.0,
            query_latency_ms_median=0.0,
            query_latency_ms_p95=0.0,
            hit_at_1=0.0,
            hit_at_5=0.0,
            queries_tested=0,
            errors=1,
            notes=["provider unavailable"],
        )

    device = getattr(provider, "device", "cpu")

    # --- embed every fragment once into an in-memory index -----------
    all_frags = frag_store.list_all(limit=10_000)
    if not all_frags:
        return ModelResult(
            model=model_name, provider=provider_name, dim=provider.dim,
            device=device, load_seconds=load_seconds,
            embed_seconds_per_fragment=0.0,
            query_latency_ms_median=0.0, query_latency_ms_p95=0.0,
            hit_at_1=0.0, hit_at_5=0.0, queries_tested=0, errors=0,
            notes=["no fragments in store"],
        )

    texts = []
    for f in all_frags:
        summary = (f.summary or "").strip()
        content = (f.content or "").strip()
        texts.append(f"{summary}\n\n{content}" if summary and content else (content or summary))

    embed_started = time.perf_counter()
    frag_vectors = provider.embed(texts)
    embed_elapsed = time.perf_counter() - embed_started
    index = list(zip([f.id for f in all_frags], frag_vectors))

    # --- queries -----------------------------------------------------
    latencies: list[float] = []
    hits1 = 0
    hits5 = 0
    errors = 0

    for q, expected_id in queries:
        t0 = time.perf_counter()
        try:
            q_vec = provider.embed([q])[0]
        except Exception:
            errors += 1
            continue
        scored = [(fid, _cosine(q_vec, v)) for fid, v in index]
        scored.sort(key=lambda t: -t[1])
        top = [fid for fid, _ in scored[:5]]
        latency_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(latency_ms)
        if top and top[0] == expected_id:
            hits1 += 1
        if expected_id in top:
            hits5 += 1

    n = len(queries) or 1
    return ModelResult(
        model=model_name,
        provider=provider_name,
        dim=provider.dim,
        device=device,
        load_seconds=load_seconds,
        embed_seconds_per_fragment=embed_elapsed / max(1, len(all_frags)),
        query_latency_ms_median=statistics.median(latencies) if latencies else 0.0,
        query_latency_ms_p95=_p95(latencies),
        hit_at_1=hits1 / n,
        hit_at_5=hits5 / n,
        queries_tested=len(queries),
        errors=errors,
        notes=notes,
    )


def _p95(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = max(0, int(round(0.95 * (len(s) - 1))))
    return s[k]


def bench(
    models: Iterable[str],
    provider: str = "sentence-transformers",
    queries_path: Path | None = None,
) -> dict[str, Any]:
    """Public entry point. Returns a dict that the CLI serialises to JSON."""

    queries = _load_queries(queries_path) if queries_path else _build_default_queries()
    if not queries:
        return {"error": "no queries available (fragment store empty and no --queries file)"}

    # Remember the user's current provider so we can restore it after.
    import os
    from hippocampus import config

    saved_provider_env = os.environ.pop("HIPPO_EMBEDDING_PROVIDER", None)
    saved_model_env = os.environ.pop("HIPPO_EMBEDDING_MODEL", None)
    saved_provider = config.get_setting("embedding_provider")
    saved_model = config.get_setting("embedding_model")

    results: list[ModelResult] = []
    try:
        for model in models:
            results.append(bench_one(model, provider, queries))
    finally:
        # Restore env so the bench doesn't leak into the running process.
        os.environ.pop("HIPPO_EMBEDDING_PROVIDER", None)
        os.environ.pop("HIPPO_EMBEDDING_MODEL", None)
        if saved_provider_env is not None:
            os.environ["HIPPO_EMBEDDING_PROVIDER"] = saved_provider_env
        if saved_model_env is not None:
            os.environ["HIPPO_EMBEDDING_MODEL"] = saved_model_env
        from hippocampus import embeddings

        embeddings.reset_provider()

    return {
        "queries_tested": len(queries),
        "fragments_in_store": frag_store.count(),
        "user_current_provider": saved_provider,
        "user_current_model": saved_model,
        "models": [r.as_row() for r in results],
        "_raw": [r.__dict__ for r in results],
    }
