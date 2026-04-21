# Tasks — Hippocampus V0.2: Working-Memory Ledger

## Relevant Files

- `migrations/002_session_ledger.sql` — new table `session_ledger`.
- `src/hippocampus/storage/ledger.py` — CRUD for ledger entries.
- `src/hippocampus/storage/sessions.py` — extend with session rotation helper.
- `src/hippocampus/mcp/tools.py` — add `log_progress`, `get_progress`, `end_progress`.
- `src/hippocampus/mcp/server.py` — register the three new MCP tools.
- `src/hippocampus/clients/injector.py` — new `format_working_block()` + second marker constants.
- `src/hippocampus/config.py` — markers + size caps constants.
- `src/hippocampus/cli/main.py` — new `hippo progress` subcommand group.
- `tests/unit/test_ledger.py` — CRUD + rendering + dedup.
- `tests/integration/test_working_block.py` — round-trip: log → upsert → read back from file.
- `tests/e2e/test_session_rotation.py` — open → log → rotate → verify old archived, new fresh.
- `docs/ARCHITECTURE.md` — add working-memory section.
- `docs/RUNBOOK.md` — new "Working with the ledger" section.

## Tasks

- [x] 0.0 Feature branch
  - [x] 0.1 `git checkout -b feature/working-memory` off `feature/hippocampus-v1`

- [x] 1.0 Schema + storage
  - [x] 1.1 Write `migrations/002_session_ledger.sql`: `session_ledger` table + indexes
  - [x] 1.2 Implement `storage/ledger.py`: `log_entry()`, `current_entries(session_id)`, `all_entries(session_id)`, `dedupe_window_check()`, `finalize_session(session_id)`
  - [x] 1.3 Extend `storage/sessions.py` with `rotate(client)` — closes the old session for the client if any, opens a new one
  - [x] 1.4 Unit tests for ledger CRUD + dedup

- [x] 2.0 Injection block
  - [x] 2.1 Add `WORKING_MARKER_START` / `WORKING_MARKER_END` to `config.py`
  - [x] 2.2 Implement `injector.format_working_block(session_id, entries)` — protocol header + sectioned entries, bounded sizes
  - [x] 2.3 Implement `injector.upsert_working_block(path, content)` (or reuse `upsert_block` with different markers)
  - [x] 2.4 Unit tests for rendering edge cases (no session, overflow, missing goal)

- [x] 3.0 MCP tools + auto-reinjection
  - [x] 3.1 Add `mcp.tools.log_progress(kind, content, details?)` — validates kind, dedupe check, appends, triggers `_refresh_working_block(client)`
  - [x] 3.2 Add `mcp.tools.get_progress(full=False, client?)` — returns ledger payload
  - [x] 3.3 Add `mcp.tools.end_progress(distill_to_fragment, summary?)` — rotates session, optionally creates fragment, refreshes blocks
  - [x] 3.4 Register new tools in `mcp/server.py` with proper input schemas
  - [x] 3.5 Implement `_refresh_working_block(client)` helper — regenerates the WORKING block in that client's rules file only (fast path, idempotent)

- [x] 4.0 CLI
  - [x] 4.1 Add `hippo progress log <kind> <content>` command
  - [x] 4.2 Add `hippo progress show [--client ...]` command
  - [x] 4.3 Add `hippo progress end [--distill] [--summary ...]` command
  - [x] 4.4 Add `hippo progress clear --confirm` command
  - [x] 4.5 Integration tests for CLI happy paths

- [x] 5.0 Inject command + daemon
  - [x] 5.1 Extend `hippo inject` to upsert BOTH blocks (long-term + working) per client
  - [x] 5.2 The launchd inject agent (existing) picks this up automatically — verify via test
  - [x] 5.3 Doctor: verify both marker pairs present per client

- [x] 6.0 Tests
  - [x] 6.1 `tests/unit/test_ledger.py` — CRUD, dedup, finalize
  - [x] 6.2 `tests/integration/test_working_block.py` — end-to-end log → file upsert → parse back
  - [x] 6.3 `tests/e2e/test_session_rotation.py` — rotation archives old ledger, block shows only new
  - [x] 6.4 Full suite must stay green (≥32 existing + new ones)

- [x] 7.0 Docs + release
  - [x] 7.1 Update `docs/ARCHITECTURE.md` with the two-block layout
  - [x] 7.2 Add `docs/RUNBOOK.md` section on "Using the working-memory ledger"
  - [x] 7.3 Update README quickstart with `log_progress` example
  - [x] 7.4 Run `hippo inject --commit` to push new blocks into all clients
  - [x] 7.5 Run `hippo doctor`
  - [x] 7.6 Commit, tag `v0.2.0`
