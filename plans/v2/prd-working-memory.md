---
date: 2026-04-20
status: draft
version: 0.2.0
owner: anon
depends_on: prd-hippocampus-v1.md
---

# PRD — Hippocampus V0.2: Working-Memory Ledger

## 1. Introduction / Overview

V1 shipped **long-term memory** (fragments with confidence, decay, injection). This V0.2 adds **short-term / working memory**: a per-session ledger that captures what the user asked and what the AI did, so every new turn — even after context compaction — starts with a trustworthy map of the current task.

**Problem:** AI clients' built-in compaction summaries are opaque and lossy. After a compaction, the AI may forget: the current goal, what's been done, what decisions were made, what's still blocked. V1's fragment block carries long-term knowledge but has no notion of "the task you're doing right now".

**Goal:** A second always-on block, updated every turn, that tells the AI (and the user) exactly where the current task stands. Compaction-proof by construction, because it lives in the rules file that every client re-injects.

## 2. Goals

1. **G1** — Auto-captured ledger of every ask + done + decision + blocker in the current session.
2. **G2** — Block is regenerated after every `log_progress` call, so the next turn sees an up-to-date state.
3. **G3** — Cross-client: any AI client (Devin, Claude Code, OpenCode, Windsurf, Antigravity) writes to and reads from the same ledger.
4. **G4** — Survives compaction via the same injection mechanism V1 uses.
5. **G5** — Scoped per-session; a new session starts a fresh ledger (but the old one is archived, never deleted).
6. **G6** — Bounded size: at most 30 most-recent entries shown in the block; full history lives in SQLite.
7. **G7** — The AI can optionally distill the closing ledger into a long-term fragment at session end.

## 3. User Stories

- **As a user**, when the AI gets compacted mid-task, I want it to know what I asked 30 minutes ago without me restating it.
- **As a user**, when I switch from Devin to Claude Code mid-task, I want the new client to immediately know the task state.
- **As the AI**, I want a clear, structured summary of the current goal, asks, completed work, and blockers injected on every turn.
- **As the AI**, when the user confirms "done", I want a protocol to record that (so my future self remembers).
- **As a user**, at session end, I want the option to distill the whole ledger into one fragment for long-term memory.

## 4. Functional Requirements

### 4.1 Storage

1. **FR-1** A new table `session_ledger(id, session_id, client, turn_index, kind, content, details, created_at)` stores entries.
2. **FR-2** `kind` ∈ `{"goal", "ask", "done", "blocker", "decision", "next", "note"}`.
3. **FR-3** `turn_index` auto-increments per session.
4. **FR-4** When a client opens a new session, any previous session for that client must be closed first. The old ledger is preserved (never deleted).
5. **FR-5** Schema change goes into a new migration `002_session_ledger.sql` so existing installs upgrade cleanly.

### 4.2 MCP tools

6. **FR-6 — `log_progress(kind, content, details?)`**: append an entry. Auto-resolves session_id from `HIPPOCAMPUS_CLIENT`. Triggers immediate re-injection into all clients (so the block reflects the new state on the next turn).
7. **FR-7 — `get_progress(client?)`**: return the current ledger for the calling or specified client.
8. **FR-8 — `end_progress(distill_to_fragment=False, summary?)`**: close the current session. If `distill_to_fragment=True`, create a long-term fragment summarising the ledger.

### 4.3 Injection block

9. **FR-9** A second marker pair `<!-- HIPPOCAMPUS:WORKING:START -->` / `<!-- HIPPOCAMPUS:WORKING:END -->` is upserted into every client's rules file.
10. **FR-10** The block renders:
    - Session header (id, client, start time, current turn count)
    - Current goal (first `goal` entry or first `ask` if no goal set)
    - Latest 10 `ask` entries
    - Latest 10 `done` entries
    - Open `blocker` entries (unresolved)
    - All `decision` entries
    - Latest 5 `next` entries
    - Latest 5 `note` entries
11. **FR-11** The block includes an explicit "Memory protocol" section at the top telling the AI *when* to call `log_progress` and with what `kind`.
12. **FR-12** If no session is active, the block shows `_No active session._` and the protocol text only.

### 4.4 CLI

13. **FR-13** New subcommand group `hippo progress` with:
    - `hippo progress log <kind> <content> [--details ...]`
    - `hippo progress show [--client <name>]`
    - `hippo progress end [--distill] [--summary "..."]`
    - `hippo progress clear [--confirm]` (admin — drops current ledger entries)

### 4.5 Behaviour

14. **FR-14** `log_progress` is idempotent: identical successive entries with identical content within 1 minute are deduped.
15. **FR-15** The ledger is bounded in the rendered block, never in storage. Full history is always queryable via `get_progress(full=True)`.
16. **FR-16** On session rotation (open new for client X), the old session's ledger is *archived* (kept in the table, but no longer shown in the block). The new session starts fresh.
17. **FR-17** Per-client isolation: Devin's block shows Devin's session ledger. Claude Code's block shows Claude Code's session ledger. They are not merged.

## 5. Non-Goals

- **N-1** Cross-client ledger merge (each client sees its own session; long-term fragments are still shared).
- **N-2** Automatic ask-extraction from the client's raw conversation (the AI must call `log_progress`).
- **N-3** Real-time collaborative editing by multiple users.

## 6. Technical Considerations

- **Regeneration cost**: Every `log_progress` call rewrites the WORKING block in 5 rules files. Each rewrite is a hash-checked idempotent upsert, so if nothing changes the write is skipped. Typical cost: 5 × ~2 KB writes per MCP call.
- **Block size budget**: Keep the WORKING block under 3 KB so it fits comfortably in any client's context window after compaction.
- **Client identity**: Derived from the `HIPPOCAMPUS_CLIENT` env var set by the MCP config. When running from CLI, user can pass `--client` or default to `cli`.
- **Testing strategy**: Tests cover (a) ledger CRUD, (b) block rendering with various entry mixes, (c) session rotation, (d) idempotent dedup, (e) end-to-end — log across two "sessions" and verify the block accurately reflects the latest.

## 7. Success Metrics

1. **M-1** After compaction, the AI correctly answers "what were you just working on?" without rereading the whole history.
2. **M-2** Switching from Devin to Claude Code mid-task → Claude Code sees the current goal and most recent asks within its first turn.
3. **M-3** Every tool call in a session produces exactly one ledger entry; nothing is silently lost.
4. **M-4** Block rendering stays under 3 KB across 30+ turn sessions.

## 8. Open Questions

- **Q-1** Should `log_progress` also automatically tag long-term fragments it touches? (V0.2 says no; V0.3 maybe.)
- **Q-2** Should we offer an "undo last entry" tool? (V0.2 says no — the AI should just log a correction as a `note`.)
- **Q-3** Per-client block vs. a shared "any-client" block? V0.2: per-client for isolation; V0.3 may revisit.
