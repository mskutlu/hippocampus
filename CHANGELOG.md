# Changelog

All notable changes to Hippocampus are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.4.1] - 2026-04-20

### Changed
- Default vault path: `~/devin-vault/` → `~/hippocampus-vault/`.
  Existing users keep their data by exporting
  `HIPPOCAMPUS_VAULT=$HOME/devin-vault` in their shell rc, or by
  running `bash scripts/install.sh` with that env var set.
- All docs, install script, config defaults, and test references
  updated to the new path.

### Fixed
- Hardened `.gitignore` with comprehensive runtime-state, secrets,
  backups, local-config, and model-cache patterns so a public
  checkout can never accidentally commit your fragment content,
  API keys, or rules-file backups.
- Removed personally identifying strings from tracked files. Author
  handle (`msk`) is kept per owner preference.

## [1.4.0] - 2026-04-20

### Added — Auto-trigger via lifecycle hooks
- **`SessionStart` hook** — fires automatically when a new Devin or
  Claude Code session begins. Opens a Hippocampus session and injects
  the full memory protocol into the model's context via
  `hookSpecificOutput.additionalContext`. **No user typing required.**
- **`UserPromptSubmit` hook** — fires on every user message. Automatically
  logs the prompt to the working-memory ledger as `kind="ask"`, so the
  AI no longer has to remember to call `log_progress` for asks.
- **`hippo install-hooks`** — registers both hooks in
  `~/.config/devin/config.json` and `~/.claude/settings.json`. Renders
  per-client shell scripts under
  `~/.config/devin/hippocampus-hooks/<client>/`. Idempotent, tagged
  with `hippocampus-v1` for surgical removal. Backups created before
  every mutation.
- **`hippo uninstall-hooks`** — removes only entries tagged
  `hippocampus-v1`; other hooks in the same config are left intact.
- **`hippo hooks-status`** — reports per-client installation state.
- **`hippo doctor`** now includes the hooks row.
- 4 new tests (install, idempotency, surgical uninstall, status).
  Total 79/79 green.

### How it changes the workflow
Before 1.4.0: the user had to paste a "use the memory protocol" nudge
as the first message of each new AI session. The protocol lived in the
rules file but was often skimmed past.

After 1.4.0: **just open Devin and type.** The SessionStart hook
injects the protocol before turn 0. The UserPromptSubmit hook captures
every ask as a ledger entry before the AI even sees the message. The
AI still does `log_progress(kind="done"|"decision"|"next")` itself,
but asks are automatic.

### Compatibility
- Devin for Terminal: uses lifecycle-hooks format per
  https://cli.devin.ai/docs/extensibility/hooks/lifecycle-hooks
- Claude Code: same format (Devin is Claude-compatible per its docs)
- Windsurf / OpenCode / Antigravity: no hooks in this release; for
  those clients we still rely on rules-file injection.

## [1.3.0] - 2026-04-20

### Added
- **`sentence-transformers` provider** (`StProvider`) with automatic
  device selection (MPS → CUDA → CPU). Unlocks the full
  Hugging Face embedder ecosystem — BGE-large, mxbai-embed-large,
  nomic-embed-text, e5-large-v2, and other models too big for
  fastembed's ONNX path.
- New optional extra **`[heavy]`** pulls `sentence-transformers`,
  `torch`, `einops`. Base and `[semantic]` installs are untouched.
- **`hippo embeddings bench`** — side-by-side benchmark of multiple
  models on YOUR actual fragment store. Runs scratch embeddings in
  memory (never touches the canonical DB), reports per-model:
  load time, embed time, p50 / p95 query latency, hit@1, hit@5,
  error count.
  - `--models "m1,m2,..."` (comma-separated)
  - `--provider {fastembed|sentence-transformers}`
  - `--queries path.jsonl` of `{query, expected_id}` rows
  - Self-retrieval fallback when no queries are supplied
- New settings: `embedding_truncate_dim` (Matryoshka) and
  `embedding_trust_remote_code`.
- Provider loader supports aliases: `st`, `sentence_transformers`,
  `sentence-transformers`.
- 3 new tests (provider fallback, stub-provider bench, empty-store
  bench). Total: 75/75 green.

### Known issues
- `dunzhang/stella_en_1.5B_v5` fails to load with
  `transformers>=4.47` because its vendored `modeling_qwen.py`
  references `config.rope_theta` which was renamed. Either pin
  `transformers<4.47` or use `gte-Qwen2-1.5B-instruct` /
  `mxbai-embed-large-v1` instead.
- Snowflake models (`snowflake-arctic-embed-*`) need
  `query: `/`passage: ` prefixes that V1.3 doesn't add yet; they
  score lower than their MTEB rating without those. Noted as a V1.4
  enhancement.

### Bench findings on current corpus (4 fragments, 12 queries)
All tested models hit 100% at rank 1. At this scale the corpus is
too small for model differences to show. The infrastructure is now
in place to re-run the bench once the corpus grows.

| Model | dim | hit@1 | p50 latency |
|---|---|---|---|
| bge-small-en-v1.5 (current) | 384 | 100% | ~180 ms |
| bge-large-en-v1.5 | 1024 | 100% | ~124 ms |
| mxbai-embed-large-v1 | 1024 | 100% | ~144 ms |
| nomic-embed-text-v1.5 | 768 | 100% | ~152 ms |
| e5-large-v2 | 1024 | 100% | ~22 ms |

Recommendation: **stay on bge-small until ≥100 fragments**, then
re-run `hippo embeddings bench`.

## [1.2.0] - 2026-04-20

### Added
- **Web UI.** A single-page local dashboard served by FastAPI at
  `http://127.0.0.1:7878`. Start with `hippo web`. Five tabs:
  - **Dashboard** — stats, top-N preview, live hybrid recall.
  - **Fragments** — browseable table, click for detail drawer with
    pin / unpin / forget / delete actions, tag filter, quick add form.
  - **Working** — per-client session ledger viewer + log form +
    undo / end-session / distill actions.
  - **Feedback** — last 100 confidence-changing events.
  - **Settings** — editable form for every `hippo config` value +
    embeddings coverage + manual reindex button.
- JSON API under `/api/*` backing every CLI and MCP action:
  - Fragments CRUD (GET/POST/DELETE + pin/unpin/forget)
  - `POST /api/recall` with full hybrid scoring response
  - Working-memory endpoints (progress, progress/end, progress/undo)
  - Embeddings (stats + reindex)
  - Config (show + set)
  - Feedback log + associations graph
- CSRF-style defence-in-depth: server generates a random token, UI
  reads `/api/csrf` once, sends it in the `X-Hippo-Token` header on
  every mutation. Bound to `127.0.0.1` by default.
- Dark-first vanilla HTML/JS UI — zero build step, one static file.
- CLI: `hippo web [--host] [--port] [--no-browser]`.
- Optional extra `[web]` in pyproject (`fastapi`, `uvicorn[standard]`).
- 7 new integration tests using FastAPI TestClient.

### Security notes
- Loopback binding by default.
- Non-loopback host (`--host 0.0.0.0`) prints a warning.
- No auth beyond the same-origin token — this is a **local** tool.

## [1.1.0] - 2026-04-20

### Added
- **Semantic recall via local embeddings.** `recall(query)` now runs a
  hybrid of FTS5 keyword search and cosine similarity over embedded
  fragments. Results are blended with `score = fts * (1 - w) + semantic * w`
  where `w = semantic_weight` (default 0.5, configurable).
- **Local embeddings.** Default provider: `fastembed` with the
  `BAAI/bge-small-en-v1.5` model (384 dims). Model is downloaded once to
  `~/.hippocampus/models/` on first use and runs fully offline. No external
  API calls, no cloud, no key management.
- **Graceful fallback.** fastembed is an **optional** extra
  (`pip install -e '.[semantic]'`). Without it, `recall` works exactly
  like V1 (FTS-only), and `hippo doctor` surfaces the missing provider.
- **New schema** (migration 003): `fragment_embeddings(fragment_id, vector,
  dim, model, created_at)`. Vectors stored as little-endian float32 bytes.
- **New CLI:**
  - `hippo reindex [--force] [--batch 64]` — embed missing (or all with
    `--force`) fragments.
  - `hippo embeddings stats` — coverage, model, dim, provider availability.
- `remember()` embeds synchronously on insert; failure is non-fatal
  (fragment still stored, re-embed later with `hippo reindex`).
- `recall()` response now includes a `scores: {fts, semantic}` field per
  hit and a top-level `semantic_available` + `semantic_weight`.
- `hippo doctor` reports embedding coverage and model.
- Config settings: `embedding_provider`, `embedding_model`, `semantic_weight`.
- 9 new tests (stub provider, pack/unpack, cosine math, hybrid recall,
  fallback).

### Changed
- `recall()` rewired to the hybrid scorer. FTS-only behaviour is preserved
  when the semantic provider is unavailable.
- `stats` dashboard now shows embedding provider / coverage.

### Dependencies
- Base install: unchanged (no new required deps).
- Optional: `fastembed>=0.3.0` via `[semantic]` extra.

## [0.3.0] - 2026-04-20

### Added
- **Shared working-block mode.** New setting `working_block_mode` ∈
  `{per_client, shared}`; in `shared` mode every client's rules file
  carries the same block (picks the most recently active session across
  all clients), so switching Devin ↔ Claude Code mid-task shows
  continuous progress.
- **Auto-tag referenced fragments.** When `log_progress` content or
  details contains a `frag_...` id, that fragment is boosted as if
  recalled with a context tag `log_progress:<kind>`. Returns a new
  `boosted_fragments` array in the response.
- **`undo_last_entry` MCP tool + `hippo progress undo` CLI.** Pops the
  most recent ledger entry. Refuses if the entry is older than 5
  minutes (use `end_progress` for older corrections).
- **Idle auto-end.** New setting `auto_end_idle_minutes` (default `None`).
  When set, the hourly decay daemon rotates any session with no
  ledger / access activity within the window.
- **Persistent settings** in `~/.hippocampus/config.json` via
  `hippo config show` and `hippo config set <key> <value>`. Env vars
  prefixed with `HIPPO_` still win for tests and ad-hoc overrides.
- **Bug fix (v0.2 regression):** the four working-memory tools
  (`log_progress`, `get_progress`, `end_progress`, `undo_last_entry`)
  are now exposed in the MCP `TOOL_SPECS` list. V0.2 had them in the
  dispatcher but not in the tool registry, so clients couldn't see them.
- `hippo doctor` now reports the effective settings.
- 11 new tests.

### Changed
- `dynamics/decay.run_decay_cycle` calls `auto_end_idle_sessions()`
  before decay, so stale sessions are rotated just in time for the
  shield window calculation.
- `clients/injector.upsert_block` accepts optional
  `marker_start` / `marker_end` so the same helper drives both blocks.

## [0.2.1] - 2026-04-20

### Added
- `CHANGELOG.md` (this file).
- Working-memory usage examples in `README.md` quickstart.

### Changed
- Checkboxes in `plans/v1/tasks-hippocampus-v1.md` and
  `plans/v2/tasks-working-memory.md` now reflect reality.

## [0.2.0] - 2026-04-20

### Added
- **Working-memory ledger** (short-term memory, per-session).
  A second always-on block (`<!-- HIPPOCAMPUS:WORKING:START/END -->`)
  inside every client's rules file holds the current session's
  asks/dones/decisions/blockers. Regenerated immediately on every
  `log_progress` call so it survives compaction.
- Schema: `session_ledger` table (migration 002). Columns:
  `id`, `session_id`, `client`, `turn_index`, `kind`, `content`,
  `details`, `resolved`, `created_at`.
- 3 new MCP tools:
  - `log_progress(kind, content, details?)` — append an entry,
    refresh the WORKING block.
  - `get_progress(full=false, client?)` — read the current ledger.
  - `end_progress(distill_to_fragment?, summary?, tags?)` — close
    the current session; optionally distill everything to a single
    long-term fragment.
- `kind` enum: `goal | ask | done | blocker | decision | next | note`.
- 60-second dedup window so aggressive logging doesn't spam.
- Per-client session isolation: Devin, Claude Code, OpenCode, Windsurf,
  and Antigravity each have their own ledger.
- CLI: `hippo progress log|show|end|clear`.
- `sessions.rotate(client)` — closes the current session for a client
  and opens a fresh one. Old entries are preserved in the DB.
- `hippo doctor` now reports `long:✓ working:✓ mcp:✓` per client.
- `hippo strip-blocks` now removes both markers.
- Strong "memory protocol" header inside the block instructing the AI
  to call `log_progress` reflexively.
- 14 new tests (9 ledger, 2 rendering, 3 integration) — 46/46 green.

### Changed
- `clients/injector.upsert_block()` now accepts optional
  `marker_start` / `marker_end` kwargs.
- `hippo inject` writes BOTH blocks per client.
- `docs/ARCHITECTURE.md` updated for two-block layout + 12-tool surface.
- `docs/RUNBOOK.md` gained a working-memory section.

## [0.1.0] - 2026-04-20

Initial release.

### Added
- Canonical SQLite store at `~/.hippocampus/hippocampus.db` +
  human-readable Obsidian mirror at `~/hippocampus-vault/Fragments/`.
- Biological dynamics per spec:
  - Boost on access: `+0.015`, capped at `1.0`.
  - Decay per session for unused, non-pinned fragments: `-0.002`,
    floored at `0.0`.
  - Shield: fragments accessed in current or previous session
    skip decay.
  - Pin: pinned fragments never decay.
  - Negative feedback via `forget()`: `-0.02`.
  - Auto-archive: confidence below `0.05` for 7+ days → moves mirror
    to `Fragments/.archive/`, removes SQLite row.
  - Associations: fragments returned together accumulate weighted edges.
  - No time-based decay — confidence changes only on access, feedback,
    or explicit decay cycle.
- Python MCP server (`hippocampus-mcp`, stdio) with 9 tools:
  `recall`, `remember`, `forget`, `pin`, `unpin`, `get_fragment`,
  `list_fragments`, `top_fragments`, `get_stats`.
- Auto-injection of the top-N fragments as a marker-delimited block
  inside each AI client's global rules file
  (`<!-- HIPPOCAMPUS:START/END -->`). Writes are idempotent and
  hash-checked; first mutation leaves a `*.pre-hippocampus.bak`.
- launchd agents for hourly decay, 10-minute inject, daily archive.
- CLI `hippo` with subcommands: `init`, `doctor`, `session`, `remember`,
  `recall`, `forget`, `pin`, `unpin`, `stats`, `list`, `top`, `decay`,
  `archive`, `inject`, `register`, `unregister`, `strip-blocks`.
- Client registry for Devin, Claude Code, OpenCode, Windsurf, Antigravity.
- `HIPPOCAMPUS_CLIENT` env var passed to the MCP server by each client's
  MCP config so the server correctly scopes session tracking.
- 32 tests (unit + integration + e2e), all green.
- Docs: README, ARCHITECTURE, RUNBOOK, PRD, task list.
- One-command install: `bash scripts/install.sh`.
- Clean reversal: `bash scripts/uninstall.sh` +
  `rm -rf ~/.hippocampus ~/hippocampus-vault/Fragments` for data.

[0.2.1]: https://example.invalid/compare/v0.2.0...v0.2.1
[0.2.0]: https://example.invalid/compare/v0.1.0...v0.2.0
[0.1.0]: https://example.invalid/releases/tag/v0.1.0
