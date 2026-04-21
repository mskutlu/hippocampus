# Architecture

## System diagram

```
┌───────────────────────────────────────────────────────────────┐
│ AI Clients (each over MCP stdio)                              │
│  Devin · Claude Code · OpenCode · Windsurf · Antigravity      │
└───────────────────────┬───────────────────────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────────────────────┐
│ hippocampus.mcp.server (Python stdio MCP server)              │
│   recall · remember · forget · pin · unpin                    │
│   get_fragment · list_fragments · top_fragments · get_stats   │
└───────────┬──────────────────────────────┬────────────────────┘
            │                              │
            ▼                              ▼
┌──────────────────────────┐   ┌────────────────────────────────┐
│ SQLite (canonical)       │ ←→│ Obsidian mirror (markdown)     │
│ ~/.hippocampus/          │   │ ~/hippocampus-vault/Fragments/*.md   │
│   hippocampus.db         │   │   .archive/*.md                │
└───────────┬──────────────┘   └────────────────────────────────┘
            ▲
            │ periodic jobs
            │   decay    (every hour)
            │   inject   (every 10 minutes)
            │   archive  (every 24 hours)
            │
┌───────────┴───────────────────────────────────────────────────┐
│ launchd agents                                                │
│   com.hippocampus.daemon.plist                                │
│   com.hippocampus.inject.plist                                │
│   com.hippocampus.archive.plist                               │
└───────────────────────────────────────────────────────────────┘
            │
            ▼ hippo inject
┌───────────────────────────────────────────────────────────────┐
│ _HIPPOCAMPUS_CONTEXT.md  (top-N block, canonical copy)        │
│   upserted as a marker-delimited block into each client's     │
│   always-on rules file:                                       │
│   ~/.config/devin/AGENTS.md                                   │
│   ~/.claude/CLAUDE.md                                         │
│   ~/.config/opencode/AGENTS.md                                │
│   ~/.codeium/windsurf/memories/global_rules.md                │
│   ~/.antigravity/rules/global_rules.md                        │
└───────────────────────────────────────────────────────────────┘
```

## Data model

### `fragments`

One row per atomic synthesized memory. Columns of interest:

| column | type | notes |
|---|---|---|
| `id` | TEXT PK | `frag_<ULID>` |
| `content` | TEXT | full synthesized text |
| `summary` | TEXT | short one-liner shown in the injection block |
| `confidence` | REAL | `[0.0, 1.0]`, init 0.5 |
| `accessed` | INTEGER | monotonic access counter |
| `last_accessed_at` | TEXT | ISO-8601 UTC |
| `pinned` | INTEGER | 0/1; pinned rows never decay |
| `below_threshold_since` | TEXT | set the first time confidence dips below `ARCHIVE_THRESHOLD`; cleared on recovery |

### `fragment_tags`

Many-to-many tag assignments. Tags accumulate organically — `context_tag` on
`recall` adds to the tag set, so a fragment recalled during debugging gains a
`debugging` tag automatically.

### `associations`

Undirected co-access edges. We enforce `fragment_a < fragment_b` as a
canonical ordering so `(A,B)` and `(B,A)` collapse to a single row. Each row
carries a `weight` (starts at 1.0, grows by 1.0 per co-access) and a
`co_accessed_count` integer for diagnostics.

### `sessions` + `session_accesses`

Each AI client opens a session on first use. Every fragment access is logged
against the current session id. The decay loop consults "was this fragment
touched in the current or previous session?" to build the shield.

### `feedback_log`

Append-only audit trail: `boost`, `decay`, `negative`, `pin`, `unpin`,
`archive`. Useful for postmortem when confidences drift.

### `fragments_fts` (virtual)

FTS5 mirror of `(content, summary)`. Triggers keep it synchronised on every
insert/update/delete.

## Biological model (exact formulas)

| Event | Formula | Notes |
|---|---|---|
| Boost on access | `confidence = min(1.0, confidence + 0.015)` | +1 to `accessed`, set `last_accessed_at`, optional context tag, clear `below_threshold_since` |
| Negative feedback | `confidence = max(0.0, confidence - 0.02)` | logged in `feedback_log` |
| Decay per cycle | `confidence = max(0.0, confidence - 0.002)` | only if NOT pinned AND NOT accessed in current/previous session |
| Shield | `session_accesses ∩ last 2 sessions` | anything in that set is protected in this decay cycle |
| Recency factor | `exp(-days_since_last_access / 14)` | 1 for brand-new, ~0.37 at two weeks |
| Composite score | `confidence*0.7 + recency*0.3` | used for ranking, not for decay |
| Archive threshold | confidence < 0.05 for 7+ days | moves to `.archive/`, removes from SQLite |

No time-based decay. Confidence changes only on access-boost, explicit
feedback, or an explicit decay cycle.

## Injection pipeline

1. `hippo inject` (also run by the launchd inject agent every 10 minutes) asks
   `hippocampus.dynamics.ranking.top_n(limit=15)` for the highest-scoring
   fragments.
2. The result is rendered with `clients.injector.format_injection_block()` —
   a markdown block wrapped in `<!-- HIPPOCAMPUS:START --> ... END -->`.
3. The canonical file at `~/.hippocampus/_HIPPOCAMPUS_CONTEXT.md` is
   overwritten atomically.
4. For each registered client, `upsert_block()` locates the marker pair in
   that client's rules file and replaces the body (or appends if missing).
   The very first mutation leaves a `*.pre-hippocampus.bak` copy next to the
   file so the user can always recover the pristine state.
5. If the new candidate hashes identically to the existing file content, the
   write is skipped (avoids file-thrashing and spurious editor reloads).

## MCP surface

Twelve tools, all plain Python functions in `hippocampus.mcp.tools` so the
CLI, tests, and direct callers use the same code path as MCP clients.

| Layer | Tool | Purpose |
|---|---|---|
| long-term | `recall` | FTS + boost |
| long-term | `remember` | store a synthesized fragment |
| long-term | `forget` | negative feedback |
| long-term | `pin` / `unpin` | shield from decay |
| long-term | `get_fragment` | read by id (optional boost) |
| long-term | `list_fragments` | admin query |
| long-term | `top_fragments` | top-N for injection |
| long-term | `get_stats` | dashboard |
| working | `log_progress` | append a ledger entry + refresh the working block |
| working | `get_progress` | read the current ledger |
| working | `end_progress` | close session, optionally distill to long-term fragment |

## Concurrency

- SQLite runs in WAL mode with `busy_timeout=5s` and `synchronous=NORMAL`.
- Every connection is short-lived and scoped to one transaction. Multiple
  clients + the daemon can issue writes simultaneously without corruption.
- The mirror writer is idempotent — concurrent writes to the same fragment
  race to produce the same bytes.

## Extensibility

Adding a new AI client (Cursor, Continue, Zed, …) is three steps:

1. Append a `ClientSpec` entry to `src/hippocampus/clients/registry.py`.
2. (If the client uses a non-standard MCP config schema) add a branch to
   `clients/mcp_config.register()`.
3. Run `hippo register` and `hippo inject` — done.
