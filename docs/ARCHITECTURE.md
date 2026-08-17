# Architecture

## System diagram

```
┌───────────────────────────────────────────────────────────────┐
│ AI Clients (each over MCP stdio)                              │
│ Devin · Claude Code · Codex · OpenCode · Windsurf             │
│ · Antigravity · VS Code Copilot · ZCode · Zed · Hermes Agent  │
│ · Pi via extension                                            │
└───────────────────────┬───────────────────────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────────────────────┐
│ hippocampus.mcp.server (Python stdio MCP server)              │
│   recall · remember · forget · pin · unpin                    │
│   get_fragment · list_fragments · top_fragments · get_stats   │
│   log_progress · get_progress · log_transcript · get_transcript│
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
│   ~/.cursor/rules/hippocampus.mdc                             │
│   ~/.codex/AGENTS.md                                          │
│   ~/.config/opencode/AGENTS.md                                │
│   ~/.codeium/windsurf/memories/global_rules.md                │
│   ~/.antigravity/rules/global_rules.md                        │
│   ~/.copilot/instructions/hippocampus.instructions.md         │
│   ~/.zcode/AGENTS.md                                          │
│   ~/.config/zed/AGENTS.md                                     │
│   ~/.hermes/SOUL.md                                           │
│   ~/.pi/agent/AGENTS.md                                       │
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

Each AI client opens a session on first use for the current `session_key`.
The key is derived from an explicit override when present, otherwise from
terminal TTY / terminal-session hints plus cwd. This allows multiple terminals
or worktrees using the same client to keep separate ledgers. Every fragment
access is logged against the current session id. The decay loop consults
"was this fragment touched in the current or previous session?" to build the
shield.

### `session_transcript`

Append-only raw/visible conversation history for a session. This table stores
full user prompts captured by hooks, visible assistant responses when clients
or agents provide them, and explicit reasoning summaries. It is deliberately
separate from `fragments`: transcript rows are provenance, while fragments are
synthesized long-term memory. Hidden chain-of-thought should not be stored.

### `feedback_log`

Append-only audit trail: `boost`, `decay`, `negative`, `pin`, `unpin`,
`archive`. Useful for postmortem when confidences drift.

### `wiki_projects` + `wiki_pages` + `wiki_sources`

The LLM Wiki layer is project-scoped and database-backed. A project must have a
`wiki_projects` row before wiki operations can continue. If a project is
missing, CLI/MCP calls return `wiki_not_initialized` and tell the user to run
`hippo wiki init`.

`wiki_pages` stores canonical markdown bodies and metadata for source, entity,
concept, topic, analysis, overview, index, log, and schema pages. Exported
markdown files are materialized views for Obsidian or git; SQLite is canonical.

`wiki_sources`, `wiki_page_sources`, `wiki_links`, and `wiki_log` keep source
provenance, citations, wikilink graph data, and chronological activity.

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

All tools are plain Python functions in `hippocampus.mcp.tools` so the
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
| working | `log_progress` | append a ledger entry + refresh the working block + rewrite the handoff file; result echoes the current goal |
| working | `get_progress` | read the current ledger (includes current goal + handoff path) |
| working | `get_handoff` | read the session's handoff document (falls back to the previous session — the resume path) |
| working | `end_progress` | close session, optionally distill to long-term fragment; finalizes the handoff |
| transcript | `log_transcript` | store raw prompt / visible response / reasoning summary |
| transcript | `get_transcript` | read current session transcript rows |
| working | `undo_last_entry` | remove the latest ledger entry within the undo window |
| wiki | `wiki_init` | initialize project-scoped DB wiki state |
| wiki | `wiki_status` | report wiki state or `wiki_not_initialized` |
| wiki | `wiki_ingest` | ingest a markdown/text source into wiki pages |
| wiki | `wiki_query` | assemble answer context from DB wiki pages |
| wiki | `wiki_file_answer` | file an answer as an analysis page |
| wiki | `wiki_lint` | check wiki health |
| wiki | `wiki_export` | materialize DB pages to markdown |

## Concurrency

- SQLite runs in WAL mode with `busy_timeout=5s` and `synchronous=NORMAL`.
- Every connection is short-lived and scoped to one transaction. Multiple
  clients + the daemon can issue writes simultaneously without corruption.
- The mirror writer is idempotent — concurrent writes to the same fragment
  race to produce the same bytes.

## Injection vs. hooks (v1.5+)

There are two independent paths that put Hippocampus state in front of
the AI on each turn:

| Path | What it carries | When it refreshes | Limitation |
|---|---|---|---|
| **Rules-file injection** (`hippo inject`) | Top-N fragments + WORKING block | every `log_progress`, every 10 min via cron/launchd | The client reads the rules file ONCE at session start; later edits are invisible to the AI until something forces a re-read. |
| **Lifecycle hooks** (`hippo install-hooks`) | Live ledger + top fragments + protocol text | every `SessionStart`, every `UserPromptSubmit`, every Devin `PostCompaction` | Bounded by `hook_inject_budget_chars`. PostCompaction is Devin-only; Claude Code coverage is via `UserPromptSubmit`. |

The hooks path is what makes the WORKING block compaction-safe. The
rules-file path is still useful (it works when hooks aren't installed,
and the top-N block lives there permanently for direct user reference)
but should not be relied on alone for short-term memory.

## Extensibility

Adding a new AI client (Cursor, Continue, Zed, …) is three steps:

1. Append a `ClientSpec` entry to `src/hippocampus/clients/registry.py`.
2. (If the client uses a non-standard MCP config schema) add a branch to
   `clients/mcp_config.register()`.
3. Run `hippo register` and `hippo inject` — done.
