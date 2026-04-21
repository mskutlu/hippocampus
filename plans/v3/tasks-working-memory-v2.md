# Tasks — Hippocampus V0.3: Working-Memory Iterations

## Relevant Files

- `src/hippocampus/config.py` — add `WORKING_BLOCK_MODE`, `AUTO_END_IDLE_MINUTES`, JSON config loader.
- `src/hippocampus/storage/ledger.py` — `delete_last_entry()`, `latest_session_across_clients()`.
- `src/hippocampus/storage/sessions.py` — `idle_sessions(minutes)` helper.
- `src/hippocampus/clients/injector.py` — shared-mode rendering branch.
- `src/hippocampus/mcp/tools.py` — `undo_last_entry`, auto-tag scan, shared-mode injection.
- `src/hippocampus/mcp/server.py` — register `undo_last_entry`.
- `src/hippocampus/cli/main.py` — `hippo progress undo`, `hippo config set/show`.
- `src/hippocampus/dynamics/decay.py` — call idle auto-end from run_decay_cycle.
- `tests/unit/test_config.py` — JSON config file round-trip.
- `tests/unit/test_auto_tag.py` — fragment-id extraction + boost.
- `tests/integration/test_shared_mode.py` — flip mode, verify all blocks identical.
- `tests/integration/test_undo_and_idle.py` — undo + idle auto-end.

## Tasks

- [x] 0.0 Feature branch off `feature/working-memory`
  - [x] 0.1 `git checkout -b feature/working-memory-v2`

- [ ] 1.0 Config file loader
  - [ ] 1.1 Add `~/.hippocampus/config.json` schema (mode, idle_minutes)
  - [ ] 1.2 Implement `config.load_json_config()` — merges file values over defaults; env vars win over file
  - [ ] 1.3 Implement `config.set_json_value(key, value)` for `hippo config set`
  - [ ] 1.4 Add `WORKING_BLOCK_MODE` + `AUTO_END_IDLE_MINUTES` as module-level getters (re-read each call so tests can monkeypatch)
  - [ ] 1.5 Unit test

- [ ] 2.0 Shared working block
  - [ ] 2.1 `ledger.latest_session_across_clients()` — returns (session_id, client, started_at)
  - [ ] 2.2 Modify `_refresh_working_block(client)` to respect WORKING_BLOCK_MODE
  - [ ] 2.3 When `shared`, protocol header reads "Shared ledger across all clients"
  - [ ] 2.4 Integration test: log from 2 clients, flip mode, verify both rules files contain identical content

- [ ] 3.0 Auto-tag referenced fragments
  - [ ] 3.1 Helper `_extract_fragment_ids(text: str) -> list[str]`
  - [ ] 3.2 In `log_progress`, after writing the entry, scan content+details for fragment ids; for each existing fragment, call `boost` with `context_tag="log_progress:<kind>"`
  - [ ] 3.3 Unit test on extraction + the full log_progress → boost path

- [ ] 4.0 Undo
  - [ ] 4.1 `ledger.delete_last_entry(session_id)` returns the deleted entry or None
  - [ ] 4.2 MCP tool `undo_last_entry(client?)` refuses if entry is older than 5 minutes
  - [ ] 4.3 Log feedback event with `kind="undo"`
  - [ ] 4.4 Refresh working block
  - [ ] 4.5 Unit + integration tests

- [ ] 5.0 Idle auto-end
  - [ ] 5.1 `sessions.idle_sessions(minutes)` returns open sessions whose last access is older than the window
  - [ ] 5.2 `dynamics.decay.run_decay_cycle` calls `auto_end_idle_sessions()` when AUTO_END_IDLE_MINUTES set
  - [ ] 5.3 Integration test

- [ ] 6.0 CLI
  - [ ] 6.1 `hippo progress undo`
  - [ ] 6.2 `hippo config` group with `show` and `set <key> <value>`
  - [ ] 6.3 `hippo doctor` shows current mode + idle minutes

- [ ] 7.0 Release
  - [ ] 7.1 All tests green (≥46)
  - [ ] 7.2 README + CHANGELOG updated
  - [ ] 7.3 Commit, tag v0.3.0, `hippo inject --commit`
