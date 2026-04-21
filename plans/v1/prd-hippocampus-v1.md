---
date: 2026-04-20
status: draft
version: 1.0
owner: anon
---

# PRD — Hippocampus V1

## 1. Introduction / Overview

**Hippocampus** is a shared, biologically-inspired long-term memory system for AI assistants. It acts as an external hippocampus that all your AI clients (Devin, Claude Code, OpenCode, Windsurf, Antigravity) share via MCP.

The human brain does not record everything — it synthesizes, distills, and leaves behind fragments. Frequently accessed memories strengthen; unused ones fade and are forgotten. Hippocampus implements this same model:

- **Only synthesized fragments are stored** — never raw conversations
- **Confidence decays** over time when unused (-0.002 per session)
- **Confidence boosts** on access (+0.015 per recall)
- **Frequently accessed memories are shielded** from decay
- **Co-accessed fragments build associations** automatically
- **Negative feedback** reduces confidence (-0.02 per bad recall)
- **Top-N fragments are auto-injected** into every AI client's session — the LLM sees them without calling tools

**Problem it solves:** Today your three-layer memory (session rules + memory graph + Obsidian vault) is static — no dynamics, no prioritization, no shared auto-injection across clients. Every client reads the vault independently and only the "always-on rules" layer is auto-injected.

**Goal:** Build one shared memory substrate that all AI clients can read, write, strengthen, forget, and automatically inject from.

---

## 2. Goals

1. **G1 — Fragment-level memory:** Replace note-as-unit with fragment-as-unit. Each fragment is a single synthesized idea, not a whole document.
2. **G2 — Biological dynamics:** Implement boost/decay/shield/associations exactly per spec.
3. **G3 — Cross-client shared memory:** One backend serves Devin, Claude Code, OpenCode, Windsurf, Antigravity via MCP.
4. **G4 — Automatic injection:** Top-N fragments auto-load into every client's session without tool calls.
5. **G5 — Human-readable mirror:** Every fragment is mirrored to `~/hippocampus-vault/Fragments/*.md` for manual inspection.
6. **G6 — Operable:** CLI for ops, launchd daemon for decay, simple install script.
7. **G7 — Safe:** Nothing is permanently deleted below confidence 0.05 without a grace period; pinned fragments never decay.

---

## 3. User Stories

- **As an AI user,** I want all my AI assistants to remember the same facts about my work so I don't repeat myself across tools.
- **As an AI user,** I want frequently used knowledge to stay sharp, and obsolete knowledge to fade automatically, without manually curating.
- **As an AI,** I want to be automatically given the user's highest-confidence context at session start so I don't have to search for it.
- **As an AI,** when I learn something new during a session, I want to write a fragment that my future self (across any client) will auto-receive.
- **As an AI,** when I notice a remembered fact is wrong, I want to flag it so confidence drops and it stops being injected.
- **As the user,** I want to inspect and edit my memories in Obsidian like any other note.
- **As the user,** I want to pin critical memories so they never decay.

---

## 4. Functional Requirements

### 4.1 Storage

1. **FR-1** The system must store fragments in a canonical SQLite database at `~/.hippocampus/hippocampus.db`.
2. **FR-2** Every fragment must have: `id` (ULID), `content`, `summary`, `confidence (0.0–1.0)`, `accessed (int)`, `last_accessed_at`, `created_at`, `updated_at`, `pinned (bool)`, `source_type`, `source_ref`.
3. **FR-3** Tags must be stored in a separate table (many-to-many).
4. **FR-4** Associations (fragment-to-fragment co-access) must be stored with a weight that strengthens on repeated co-access.
5. **FR-5** Every session must be logged (id, client, started_at, ended_at) and every fragment access during a session recorded.
6. **FR-6** Every fragment must be mirrored to `~/hippocampus-vault/Fragments/<id>.md` with frontmatter matching the SQLite row.
7. **FR-7** The mirror must be regenerated atomically on every fragment mutation.
8. **FR-8** Full-text search must be available via SQLite FTS5.

### 4.2 Biological Dynamics

9. **FR-9 — Boost on access:** Any `recall` or `get_fragment` call must increment `accessed` by 1 and apply `confidence = min(1.0, confidence + 0.015)`.
10. **FR-10 — Last-accessed tracking:** Every access updates `last_accessed_at` to current UTC timestamp.
11. **FR-11 — Context tag accumulation:** When a caller provides a `context_tag` on recall (e.g. "debugging"), add it to the fragment's tags if not present.
12. **FR-12 — Co-access associations:** Fragments returned together in the same recall call must have their pairwise association weight incremented by 1.0 and `last_co_accessed_at` updated.
13. **FR-13 — Decay:** A background job running every 1 hour (launchd) must apply `confidence -= 0.002` to every fragment that is:
    - Not pinned AND
    - Not accessed in the current or previous session
14. **FR-14 — Shield:** Fragments accessed in the current OR previous session must NOT decay in this cycle.
15. **FR-15 — Pin:** Pinned fragments never decay regardless of access.
16. **FR-16 — Negative feedback:** A `forget(fragment_id)` call must apply `confidence -= 0.02` (floor 0.0) and log the event.
17. **FR-17 — Auto-prune:** Fragments with `confidence < 0.05` for 7+ consecutive days must be archived to `~/hippocampus-vault/Fragments/.archive/` and removed from SQLite.
18. **FR-18 — No time-based decay:** Confidence must change only via access boost, session decay, or explicit feedback. No wall-clock decay.

### 4.3 MCP Server

19. **FR-19** Expose a stdio MCP server at `~/IdeaProjects/hippocampus/src/hippocampus/mcp/server.py` with the following tools:

| Tool | Purpose | Boosts? |
|------|---------|---------|
| `recall(query, limit=5, min_confidence=0.0, context_tag=None)` | Semantic + FTS search | Yes |
| `remember(content, summary?, tags?, source_type?, source_ref?)` | Store new fragment | No |
| `forget(fragment_id, reason?)` | Apply -0.02 negative feedback | No |
| `pin(fragment_id)` / `unpin(fragment_id)` | Shield from decay | No |
| `get_fragment(fragment_id)` | Read one by ID | Yes |
| `list_fragments(tag?, min_confidence?, limit=20)` | Admin query | No |
| `top_fragments(limit=10)` | Highest-ranking for injection | No |
| `get_stats()` | Health dashboard | No |

20. **FR-20** Every MCP call must be logged to `~/.hippocampus/logs/mcp-<date>.log`.

### 4.4 Session Management

21. **FR-21** Each client must open a session on startup by calling a CLI helper: `hippo session start --client <name>`.
22. **FR-22** Session IDs are ULIDs; the current session ID is stored in `~/.hippocampus/current-session-<client>.txt`.
23. **FR-23** Closing a session is best-effort; sessions older than 24h are auto-closed.

### 4.5 Automatic Injection

24. **FR-24** A single canonical injection file must be generated at `~/.hippocampus/_HIPPOCAMPUS_CONTEXT.md` containing the top-N (default 15) fragments ranked by `confidence × recency_factor`.
25. **FR-25** The injection file format is a marker-delimited block:
    ```
    <!-- HIPPOCAMPUS:START -->
    ...top-N fragments as bullet list...
    <!-- HIPPOCAMPUS:END -->
    ```
26. **FR-26** A script `hippo inject` must idempotently insert/replace that block in each client's always-on rules file:
    - Devin: `~/.config/devin/AGENTS.md`
    - Claude Code: `~/.claude/CLAUDE.md`
    - OpenCode: `~/.opencode/AGENTS.md` (or equivalent per OpenCode config)
    - Windsurf: `~/.windsurf/rules/hippocampus.md` (always-on rule file)
    - Antigravity: same pattern as Windsurf/Codeium
27. **FR-27** `hippo inject` must run automatically on session-start AND every 10 minutes (to reflect new memories).
28. **FR-28** If the top-N changes by less than 5% rank-weighted, skip write (avoid file thrashing).

### 4.6 CLI

29. **FR-29** Command: `hippo` with subcommands `init`, `session`, `inject`, `stats`, `recall`, `remember`, `pin`, `unpin`, `forget`, `decay`, `archive`, `reindex`, `doctor`.
30. **FR-30** `hippo doctor` must check: SQLite exists, vault mirror is in sync, daemon is running, each client has the injection block.

### 4.7 Daemon

31. **FR-31** A launchd agent `com.hippocampus.daemon.plist` installed at `~/Library/LaunchAgents/` that:
    - Runs `hippo decay` every 1 hour
    - Runs `hippo archive` every 24 hours
    - Runs `hippo inject` every 10 minutes

### 4.8 Installation

32. **FR-32** One-command install via `bash scripts/install.sh` that: creates `~/.hippocampus/`, runs migrations, installs the launchd agent, registers the MCP server in all 5 client configs, and runs `hippo doctor`.

### 4.9 Safety & Observability

33. **FR-33** Every mutation must be logged in `~/.hippocampus/logs/events.jsonl`.
34. **FR-34** SQLite must be backed up daily to `~/.hippocampus/backups/<date>.db`.
35. **FR-35** A `--dry-run` flag must be available for `decay`, `archive`, `forget`, and `inject`.

---

## 5. Non-Goals (Out of Scope for V1)

- **N-1** Embedding-based semantic search (V1 uses FTS5 only; embeddings come in V1.1).
- **N-2** Multi-user / multi-machine sync (V1 is single-user, single-machine).
- **N-3** Raw conversation ingestion (V1 assumes user or AI explicitly calls `remember` with synthesized content).
- **N-4** Web UI (CLI + Obsidian mirror are sufficient).
- **N-5** Automatic fragment distillation from existing vault notes (manual migration skill comes in V1.1).
- **N-6** Windows/Linux support (V1 targets macOS with launchd only; cron fallback is V1.1).
- **N-7** Modification of the stock `@bitbonsai/mcpvault` server; Hippocampus is a parallel MCP server, not a replacement.

---

## 6. Design Considerations

- **Fragment ID format:** ULID (lexically sortable, ~26 chars, no central coordinator needed).
- **Confidence range:** `[0.0, 1.0]`, initialized at `0.5` for new fragments.
- **Recency factor:** `exp(-days_since_last_access / 14)` — half-life of about 10 days.
- **Ranking for injection:** `score = confidence * 0.7 + recency_factor * 0.3` (configurable).
- **Obsidian mirror format:**
  ```yaml
  ---
  id: frag_01HQRSTUV...
  confidence: 0.75
  accessed: 23
  last_accessed_at: 2026-04-20T10:15:00Z
  created_at: 2026-04-11T08:14:00Z
  tags: [debugging, kafka, limemrp]
  source_type: session
  source_ref: Sessions/2026-04-11-kafka-debug.md
  associated_with: [frag_01HQR..., frag_01HQS...]
  pinned: false
  ---
  # Summary
  Kafka retries need idempotent consumers when outbox publishes with at-least-once semantics.

  # Content
  ...
  ```

---

## 7. Technical Considerations

- **Language:** Python 3.11+ (uses `uv` for env + deps).
- **MCP SDK:** Official `mcp` Python SDK (stdio transport).
- **DB:** SQLite with FTS5 (bundled in Python stdlib; no external dep).
- **Deps:** `mcp`, `ulid-py`, `pyyaml`, `click`, `pytest`.
- **File layout:**
  ```
  <repo root>/
  ├── src/hippocampus/
  │   ├── storage/      # SQLite CRUD + migrations
  │   ├── dynamics/     # boost/decay/shield/associate
  │   ├── sync/         # Obsidian mirror
  │   ├── mcp/          # MCP server
  │   ├── clients/      # Per-client injection adapters
  │   ├── cli/          # `hippo` CLI
  │   └── __init__.py
  ├── tests/
  ├── scripts/          # install.sh, launchd plist template
  ├── migrations/       # 001_initial.sql, ...
  ├── plans/v1/
  └── docs/
  ~/.hippocampus/       # runtime state (DB, logs, backups, sessions)
  ~/hippocampus-vault/Fragments/   # human-readable mirror (override via HIPPOCAMPUS_VAULT)
  ```
- **Concurrency:** SQLite WAL mode + `sqlite3` per-operation connections. MCP server and daemon may run concurrently.
- **Error handling:** All mutations in transactions. Mirror sync is idempotent (re-writing the same frontmatter is a no-op).
- **Migration strategy:** V1 assumes empty start. A `hippo migrate` command will import from vault notes in V1.1.

---

## 8. Success Metrics

1. **M-1** After 2 weeks of daily use, ≥100 fragments stored, avg confidence > 0.5, no unplanned confidence collapses.
2. **M-2** All 5 AI clients successfully auto-receive the injection block (verified by `hippo doctor`).
3. **M-3** A recall followed by a new session shows boosted confidence AND no decay for that fragment (shield works).
4. **M-4** An unused fragment shows decaying confidence session-over-session.
5. **M-5** Response time for `recall` under 50ms for a 10k-fragment store.
6. **M-6** Zero fragments lost due to sync bugs over 30 days.

---

## 9. Open Questions

1. **Q-1 — Client config paths:** OpenCode and Antigravity config paths need verification during implementation. (Mitigation: `hippo doctor` detects and reports.)
2. **Q-2 — MCP session-start trigger:** Devin, Claude Code, etc., do not expose a "session start" hook. The first recall/remember call opens a session; session close is best-effort via the 24h auto-close.
3. **Q-3 — Concurrent writes:** Multiple clients may call `remember` simultaneously; SQLite WAL + transaction isolation should handle it, but needs a stress test.
4. **Q-4 — Injection file size:** If top-15 fragments total >4KB, some clients may truncate. Mitigation: each fragment's injection form is summary-only (max 200 chars).
5. **Q-5 — Pin semantics:** Should pinning also boost confidence to 1.0, or just set `pinned=true`? V1 default: set flag only; confidence unchanged.
