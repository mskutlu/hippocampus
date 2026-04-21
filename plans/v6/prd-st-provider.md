---
date: 2026-04-20
status: draft
version: 1.3.0
owner: anon
---

# PRD — Hippocampus V1.3: sentence-transformers + MPS + Bench

## 1. Goal

Unlock high-quality embedding models (stella 1.5B, gte-Qwen2-7B,
e5-mistral-7B) on Apple Silicon via `sentence-transformers` with MPS
acceleration. Keep the existing `fastembed` path for slim installs. Add a
real benchmark tool so model-swap decisions are data-driven on the user's
own fragments.

## 2. Functional Requirements

1. **FR-1** New optional extra `[heavy]` pulling `sentence-transformers`
   and `torch`.
2. **FR-2** New provider `StProvider` that auto-detects the best device
   (`mps` → `cuda` → `cpu`) and implements the existing
   `EmbeddingProvider` protocol.
3. **FR-3** Support `trust_remote_code=True` for stella and similar
   instruction-tuned models.
4. **FR-4** Support Matryoshka truncation via a new setting
   `embedding_truncate_dim` (default `None` = full model dim).
5. **FR-5** `embedding_provider` setting now accepts `fastembed` or
   `sentence-transformers`.
6. **FR-6** Default model + provider: keep fastembed+bge-small as the
   slim-install default so nothing changes without opt-in.
7. **FR-7** New CLI: `hippo embeddings bench`
   - `--models "m1,m2,..."` — comma-separated list of models
   - `--provider {fastembed|sentence-transformers}` — provider override
     applied to every model in the list
   - `--queries <path>` — JSONL of `{"query": "...", "expected_id":
     "frag_..."}`; if omitted, auto-generates queries from each
     fragment's summary (self-retrieval test)
   - For each model: temp DB → load model → embed all current
     fragments → run each query → record top-1 hit / top-5 hit / mean
     latency / embed time. Report as table.
   - Does NOT touch the user's real DB or embeddings — uses a scratch
     SQLite + in-memory vectors.
8. **FR-8** `hippo doctor` reports current provider + device.
9. **FR-9** Graceful degradation: if torch/sentence-transformers are not
   installed and the user sets the provider anyway, fall back to
   fastembed with a warning (don't crash the MCP server).

## 3. Non-Goals

- **N-1** No GPU requirement — MPS is preferred but CPU still works.
- **N-2** No reranker in this version — dense-only still.
- **N-3** No Ollama provider in this version (simple to add later).
- **N-4** No automatic model selection — the user picks; bench helps them
  decide.

## 4. Technical Notes

- `sentence-transformers` pulls in `torch`, `transformers`, `safetensors`
  — about 2–3 GB. Installed only via `[heavy]`.
- MPS detection via `torch.backends.mps.is_available()`.
- Stella is ~3 GB download. gte-Qwen2-7B is ~15 GB. Models cache at
  `~/.cache/huggingface/hub` by default; fastembed caches at
  `~/.hippocampus/models`.
- Bench measures warm-up + 5 embed calls + per-query timing; reports
  median + p95.

## 5. Success

- **M-1** `hippo embeddings bench` prints a side-by-side table across
  bge-small and stella_en_1.5B on the user's real fragments.
- **M-2** Swapping to stella via `hippo config set embedding-model` +
  `hippo reindex --force` just works.
- **M-3** No regressions on existing 71 tests.
