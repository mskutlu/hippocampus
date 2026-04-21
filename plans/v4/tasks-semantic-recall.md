# Tasks — Hippocampus V1.1: Semantic Recall

## Relevant Files

- `migrations/003_embeddings.sql` — `fragment_embeddings` table + delete trigger.
- `src/hippocampus/embeddings/__init__.py` — provider loader + interface.
- `src/hippocampus/embeddings/fastembed_provider.py` — fastembed adapter.
- `src/hippocampus/embeddings/store.py` — CRUD for vectors (bytes <-> numpy).
- `src/hippocampus/embeddings/search.py` — cosine similarity search.
- `src/hippocampus/config.py` — new settings: embedding_provider, embedding_model, semantic_weight.
- `src/hippocampus/storage/fragments.py` — hook embedding on create (lazy).
- `src/hippocampus/mcp/tools.py` — hybrid recall + stats.
- `src/hippocampus/cli/main.py` — `hippo reindex`, `hippo embeddings stats`.
- `pyproject.toml` — optional dependency `[project.optional-dependencies]`.
- `tests/unit/test_embeddings_store.py`
- `tests/integration/test_hybrid_recall.py`

## Tasks

- [x] 0.0 Feature branch off `feature/working-memory-v2`
  - [x] 0.1 `git checkout -b feature/semantic-recall`

- [ ] 1.0 Schema + storage
  - [ ] 1.1 `migrations/003_embeddings.sql`
  - [ ] 1.2 `embeddings/store.py`: `put()`, `get()`, `iter_all()`, `count()`, `missing_ids()`, `delete()`
  - [ ] 1.3 Byte pack/unpack: numpy -> bytes (little-endian float32) and back

- [ ] 2.0 Embedding provider abstraction
  - [ ] 2.1 `embeddings/__init__.py`: `EmbeddingProvider` protocol (`embed(texts)`, `dim`, `model`)
  - [ ] 2.2 `embeddings/fastembed_provider.py`: wraps `fastembed.TextEmbedding`, caches model in module scope
  - [ ] 2.3 `embeddings/__init__.py`: `load_provider()` reads config, graceful import error
  - [ ] 2.4 Unit test: vector dim matches config; same input ≈ same vector

- [ ] 3.0 Indexing
  - [ ] 3.1 `embeddings/search.py:upsert_for_fragment(fragment)` — compute + store one
  - [ ] 3.2 Hook into `storage.fragments.create()` via the same sync-hook mechanism used for the Obsidian mirror
  - [ ] 3.3 CLI `hippo reindex [--force] [--batch 64]`
  - [ ] 3.4 CLI `hippo embeddings stats`

- [ ] 4.0 Hybrid recall
  - [ ] 4.1 `embeddings/search.py:semantic_topk(query, k)` — compute query vec, cosine vs. all, return [(id, score)]
  - [ ] 4.2 Update `mcp.tools.recall`: merge FTS + semantic candidates by id, score blend, dedupe
  - [ ] 4.3 Graceful fallback when embeddings missing / provider unavailable
  - [ ] 4.4 Add settings: `semantic_weight`, `embedding_provider`, `embedding_model`

- [ ] 5.0 Doctor + observability
  - [ ] 5.1 `hippo doctor`: show embedded / total, model, semantic_weight
  - [ ] 5.2 Log embedding failures at WARN (stderr + log file)

- [ ] 6.0 Tests
  - [ ] 6.1 Unit: vector pack/unpack round-trip
  - [ ] 6.2 Unit: cosine ordering correctness
  - [ ] 6.3 Integration: semantic hit without FTS overlap
  - [ ] 6.4 Integration: graceful fallback when fastembed not installed (mock)
  - [ ] 6.5 Stress: 500 fragments, recall < 200 ms on CI-equivalent machine

- [ ] 7.0 Packaging + release
  - [ ] 7.1 `pyproject.toml`: optional `[semantic]` extra with fastembed + numpy
  - [ ] 7.2 README: installation variant with extras
  - [ ] 7.3 CHANGELOG v1.1.0
  - [ ] 7.4 `hippo reindex` on the live install
  - [ ] 7.5 Commit, tag v1.1.0
