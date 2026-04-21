---
date: 2026-04-20
status: draft
version: 1.1.0
owner: anon
depends_on: prd-hippocampus-v1.md
---

# PRD — Hippocampus V1.1: Semantic Recall

## 1. Introduction / Overview

V1 used SQLite FTS5 for `recall`. That's good for exact-match keyword search
but poor at concept queries (e.g. "how do we make Kafka consumers safe
against retries?" should match a fragment titled "idempotency and at-least-
once semantics" even without shared tokens).

V1.1 adds local embedding-based semantic recall alongside FTS. The two
search methods combine into a single hybrid score so `recall` returns the
best fragments regardless of whether the match is lexical or semantic.

**Privacy / offline constraint.** Embeddings MUST be computed locally — no
external API calls, no leaking fragment contents to cloud providers.

## 2. Goals

- **G1** `recall(query)` returns semantically similar fragments even when
  there is no lexical overlap.
- **G2** No regression on existing FTS-dominated queries.
- **G3** Local-only by default. External embedding providers are opt-in
  configuration.
- **G4** Small install footprint. Target: <150 MB additional download.
- **G5** Fast. Query latency <100 ms for 10k fragments on an M-series Mac.
- **G6** Works offline.

## 3. Functional Requirements

### 3.1 Storage

1. **FR-1** New migration `003_embeddings.sql` adds
   `fragment_embeddings(fragment_id TEXT PK, vector BLOB, dim INTEGER,
   model TEXT, created_at TEXT)`.
2. **FR-2** A trigger deletes the embedding row when a fragment is deleted.
3. **FR-3** Vectors stored as float32 little-endian (4 bytes × dim).

### 3.2 Embedding provider

4. **FR-4** Default provider: `fastembed` (ONNX-based, no PyTorch).
   Default model: `BAAI/bge-small-en-v1.5` (384 dim, ~130 MB download,
   downloaded on first use, cached at `~/.hippocampus/models/`).
5. **FR-5** Provider is pluggable via `config.embedding_provider` setting.
6. **FR-6** `fastembed` is an **optional** dependency (`extras_require`),
   so the base install stays slim. Attempting semantic recall without the
   extras installed falls back gracefully to FTS-only with a warning.

### 3.3 Embedding lifecycle

7. **FR-7** `remember()` embeds the new fragment synchronously (single call,
   cached model stays in memory). Failure is non-fatal — the fragment is
   still stored, just without an embedding.
8. **FR-8** CLI `hippo reindex [--force]` (re)embeds all fragments. `--force`
   rebuilds existing embeddings; default only fills missing ones.
9. **FR-9** On schema change (new default model), a bumped `model` string
   in the embedding row lets `reindex` detect and update selectively.

### 3.4 Hybrid recall

10. **FR-10** `recall(query, limit, ...)` runs **both**:
    - FTS search → candidate set A (up to 4× limit).
    - Cosine similarity between the query embedding and every fragment's
      embedding → candidate set B (up to 4× limit).
11. **FR-11** Candidates merge by id; score combines as:
    `score = fts_score_norm * (1 - semantic_weight) + cosine * semantic_weight`
    where `semantic_weight` is a setting (default `0.5`).
12. **FR-12** Top `limit` after scoring are returned; each gets boosted as
    before (+0.015).
13. **FR-13** If FTS returns zero candidates, fall back to pure semantic.
    If no embeddings exist yet, fall back to pure FTS.

### 3.5 CLI

14. **FR-14** `hippo reindex [--force]` — embed missing / all.
15. **FR-15** `hippo embeddings stats` — rows embedded, total fragments,
    default model, cache location, avg embed time.
16. **FR-16** `hippo config set semantic-weight <float>` and
    `hippo config set embedding-provider <name>`.

### 3.6 Observability

17. **FR-17** `hippo doctor` reports: embedded count / total, current model,
    semantic weight.

## 4. Non-Goals

- **N-1** Cross-language embedding models. V1.1 uses an English model.
- **N-2** Reranking models (BGE reranker, Cohere reranker). Out of scope.
- **N-3** Approximate nearest-neighbour index (HNSW, FAISS). V1.1 does a
  linear scan — fine for <10k fragments, which is our near-term scale.
- **N-4** Embedding the working-memory ledger. Only long-term fragments.

## 5. Technical Considerations

- **Cosine similarity** in Python. No sqlite-vec C extension dependency;
  keeps install simple.
- **Model load** is lazy and cached inside the MCP server process so
  subsequent queries are fast. CLI-initiated one-shots pay the load cost.
- **Backfill** is chunked (64 fragments per batch) to keep memory bounded.
- **Determinism**: the same input always produces the same vector (given a
  fixed model), so `--force` is only needed when the model changes.
- **Graceful fallback**: importing `fastembed` is wrapped in a try/except.
  Without it, `recall` works exactly like V1 (FTS-only).

## 6. Success Metrics

- **M-1** A semantic query (e.g. "avoid duplicate messages when consumer
  retries") correctly returns the pinned "idempotency" fragment even with
  no token overlap.
- **M-2** `hippo embeddings stats` shows 100% coverage after running
  `hippo reindex`.
- **M-3** `hippo recall` latency <100 ms on a 1k-fragment store.
- **M-4** Base install (without `hippocampus[semantic]` extra) still works
  and reports the absence in `hippo doctor`.

## 7. Open Questions

- **Q-1** Should we embed both the `summary` and the `content` separately
  and average their vectors? V1.1: embed `summary + "\n" + content`,
  truncated to 512 tokens.
- **Q-2** Should the web UI in v1.2 show similarity scores per result?
  V1.2 spec says yes if the API is in place.
