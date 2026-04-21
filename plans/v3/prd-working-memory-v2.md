---
date: 2026-04-20
status: draft
version: 0.3.0
owner: anon
depends_on: prd-working-memory.md
---

# PRD — Hippocampus V0.3: Working-Memory Iterations

## 1. Introduction / Overview

V0.2 shipped the working-memory ledger. After using it, four improvements
make it materially more useful:

1. **Shared cross-client block** (opt-in): one ledger visible in every
   client's rules file, so switching from Devin → Claude Code mid-task
   shows the same progress.
2. **Auto-tagging**: when `log_progress` content mentions a `frag_...` id,
   that fragment gets a boost + its tags accumulate the log entry's kind.
3. **Undo**: `undo_last_entry()` tool for when the AI logs a mistake.
4. **Idle auto-end**: `end_progress` fires automatically after N minutes of
   silence (configurable, default off).

Nothing from V0.2 breaks. Everything new is additive or opt-in.

## 2. Goals

- **G1** Per-client and shared modes coexist; user chooses via config.
- **G2** Fragments referenced in a `log_progress` content are boosted as if
  `recall`'d with context-tag=`log_progress`.
- **G3** The AI can undo a single mistake without admin tools.
- **G4** Long sessions that idle auto-close cleanly so the block doesn't
  carry stale state forever.
- **G5** Zero existing tests break.

## 3. Functional Requirements

### 3.1 Shared block mode

1. **FR-1** New config toggle `WORKING_BLOCK_MODE` ∈ {`per_client`, `shared`},
   default `per_client`.
2. **FR-2** In `shared` mode, the renderer uses a single session chosen as the
   most-recently-active across all clients. All clients' rules files show the
   same block.
3. **FR-3** The mode toggle is read at render time; switching modes is
   effective immediately via `hippo inject`.
4. **FR-4** Setting persists in `~/.hippocampus/config.json` (new).

### 3.2 Auto-tag referenced fragments

5. **FR-5** On every `log_progress` call, scan `content` and `details` for
   `frag_[A-Z0-9]+` tokens. For each matched fragment:
   - Apply the standard `boost` (+0.015) as if recalled, with
     `context_tag=log_progress:<kind>`.
   - Attach the entry's `kind` as a tag on the fragment.
6. **FR-6** If no fragments match, the call behaves exactly like V0.2.

### 3.3 Undo

7. **FR-7** New MCP tool `undo_last_entry()`: deletes the most recent ledger
   entry for the calling client's current session.
8. **FR-8** Deletion is logged to `feedback_log` with `kind="undo"`.
9. **FR-9** If the most recent entry is older than 5 minutes (clock-skew
   margin), refuse with an error — use `end_progress` instead.
10. **FR-10** Refreshes the WORKING block.

### 3.4 Idle auto-end

11. **FR-11** New config key `AUTO_END_IDLE_MINUTES` (default `None`).
12. **FR-12** The hourly decay daemon, when running, also calls an
    `auto_end_idle_sessions()` helper that checks the last entry timestamp
    per open session; if older than the configured minutes, calls
    `end_progress(distill_to_fragment=False)`.
13. **FR-13** The call is no-op when `AUTO_END_IDLE_MINUTES is None`.

### 3.5 CLI

14. **FR-14** New commands:
    - `hippo progress undo`
    - `hippo config set working-block-mode {per_client|shared}`
    - `hippo config set auto-end-idle-minutes <int>`
    - `hippo config show`
15. **FR-15** `hippo doctor` reports current mode + idle timer.

### 3.6 Migrations + storage

16. **FR-16** No schema migration required; all changes live in existing
    tables + a JSON config file.

## 4. Non-Goals

- **N-1** Multi-user sync (still V1.1 of multi-machine story).
- **N-2** Conversational "replay" of a session (V0.4 maybe).
- **N-3** Persisted per-fragment-per-kind tagging — if the AI flags the same
  fragment with `kind="done"` three times, it only gets the `done` tag once.

## 5. Technical Considerations

- **Config file**: `~/.hippocampus/config.json`. Loaded once at import time,
  re-readable via `hippo config show`. Env vars still win (for tests).
- **Fragment regex**: `frag_[0-9A-HJKMNP-TV-Z]{26}` (ULID alphabet).
- **Shared mode**: renderer picks `SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1`.
  Protocol header changes slightly: "Showing shared ledger across all clients."
- **Idle detector**: runs at most every 60 minutes (piggybacks on decay cycle).
  Adds one extra SQL query per session.

## 6. Success Metrics

- **M-1** Flipping `WORKING_BLOCK_MODE=shared` immediately shows the same
  block in all 5 clients without losing per-client data.
- **M-2** `log_progress` content with a `frag_...` id boosts that fragment
  confidence by exactly +0.015.
- **M-3** Undoing the last entry removes it from the block without affecting
  older entries.
- **M-4** Setting `AUTO_END_IDLE_MINUTES=30` and simulating a 31-minute gap
  causes the session to rotate on the next decay cycle.

## 7. Open Questions

- **Q-1** Should shared mode also merge cross-client entries in chronological
  order (vs. picking the single most-recent session)? V0.3: pick one.
- **Q-2** Should auto-end distill to a fragment by default? V0.3: no.
