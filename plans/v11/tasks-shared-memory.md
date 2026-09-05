# Tasks V11 — Shared memory: quality, project scoping, device sync

PRD: [prd-shared-memory.md](./prd-shared-memory.md)

## Relevant Files

- `src/hippocampus/dynamics/autoremember.py` - Trigger detection; gains ignore patterns and sentence guards (FR1, FR2).
- `src/hippocampus/dynamics/boost.py` - Asymptotic boost, skip already-injected fragments (FR3, FR4).
- `src/hippocampus/dynamics/ranking.py` - Tie-break by last access, project-aware `top_n` (FR5, FR18).
- `src/hippocampus/dynamics/decay.py` - Log only changed fragments plus one summary row (FR9).
- `src/hippocampus/mcp/tools.py` - `_render_ledger_as_fragment` cap, `mark` tool, project stamping, recall scope (FR6, FR10, FR16, FR17).
- `src/hippocampus/mcp/server.py` - Register `mark`, new `scope` and `project` parameters.
- `src/hippocampus/storage/fragments.py` - Tag filtering at write, `project` column in create/list/search (FR7, FR15, FR17).
- `src/hippocampus/storage/sessions.py` - Project resolution at session start (FR14, FR15).
- `src/hippocampus/storage/feedback.py` - Pruning helper (FR8).
- `src/hippocampus/maintenance.py` - Audit report, purge-noise, daily job additions (FR8, FR11, FR12).
- `src/hippocampus/projects.py` - New: `projects.json` loading, remote and path matching, backfill (FR13, FR14, FR19, FR20).
- `src/hippocampus/sync/merge.py` - New: pure merge functions shared by client and server (FR28).
- `src/hippocampus/sync/client.py` - New: device id, watermark, push and pull (FR25, FR26, FR29, FR30).
- `src/hippocampus/sync/server.py` - New: FastAPI oplog server (FR22, FR23).
- `src/hippocampus/cli/main.py` - `audit`, `purge-noise`, `project` group, `sync` group, `doctor` additions (FR11, FR12, FR19, FR24, FR31).
- `src/hippocampus/config.py` - New settings: `autoremember_ignore_patterns`, `sync_url`, `sync_token`, `sync_enabled`.
- `src/hippocampus/embeddings/search.py` - Project-filtered candidate set for semantic top-k (FR17).
- `src/hippocampus/web/server.py` - Project column and filter (FR21).
- `src/hippocampus/clients/*.py` - Rules-file block limited to global fragments (FR18).
- `scripts/hooks/user-prompt-submit.sh.template` - Pass cwd so the hook payload can resolve the project (FR18).
- `scripts/com.hippocampus.inject.plist.template` - Append `hippo sync --quiet` (FR30).
- `scripts/com.hippocampus.archive.plist.template` - Daily job calls cleanup, reindex, prune (FR8).
- `scripts/install.sh` - `--sync-server` flag (FR24).
- `migrations/009_quality.sql` - `session_accesses.via`, `fragment_tombstones` + delete trigger.
- `migrations/010_projects.sql` - `fragments.project`, `sessions.project`, indexes.
- `migrations/011_sync.sql` - `sync_state`.
- `tests/unit/test_autoremember_filters.py` - FR1, FR2.
- `tests/unit/test_boost_dynamics.py` - FR3, FR4, FR5.
- `tests/unit/test_distill_cap.py` - FR6, FR7.
- `tests/unit/test_audit.py` - FR11, FR12 on a fixture DB.
- `tests/unit/test_projects.py` - FR13, FR14, FR20.
- `tests/unit/test_recall_scope.py` - FR17, FR18.
- `tests/unit/test_sync_merge.py` - FR28, property-style cases.
- `tests/integration/test_sync_roundtrip.py` - Two temp DBs, in-process server via httpx, converge (FR22 to FR30).
- `docs/RUNBOOK.md`, `README.md`, `CHANGELOG.md` - FR33 to FR35.

### Notes

- Run tests with `uv run pytest -q`. Integration tests need the `web` and `dev` extras: `uv sync --extra web --extra dev`.
- Every phase ends with `hippo audit` against a copy of the real database in the scratchpad, never the live file. Copy with `hippo backup` then point `HIPPOCAMPUS_HOME` at a temp dir holding that copy.
- Measure `recall` latency before Phase 1 and after Phase 3 with the same 20 queries; record both numbers in the CHANGELOG entry.
- Phases are sequential. Do not start Phase 3 until Phase 2's backfill has run on the primary device.

## Instructions for Completing Tasks

**IMPORTANT:** As you complete each task, you must check it off in this markdown file by changing `- [ ]` to `- [x]`. Update the file after completing each sub-task, not just after completing an entire parent task.

## Tasks

- [x] 0.0 Create feature branch and baseline (owner chose direct commits to main; no branch)
  - [x] 0.1 `git checkout -b feature/v11-shared-memory`
  - [x] 0.2 `hippo backup` and copy the backup to the scratchpad as the audit fixture
  - [x] 0.3 Record baseline: `recall` p95 over 20 fixed queries, DB size, counts from the PRD table

- [x] 1.0 Phase 1 — Quality: stop the noise and the feedback loop
  - [x] 1.1 Add `autoremember_ignore_patterns` setting with defaults `^\s*<`, `<task-notification>`, `<system-reminder>`, `<task-id>`; apply in `detect()` before the trigger regex
  - [x] 1.2 Reject a trigger whose sentence is over 300 chars or contains `<[a-z-]+>`; test with the 5 real notification payloads from the audit
  - [x] 1.3 Change boost to `delta = BOOST_DELTA * (1 - confidence)`; keep pin and `mark(useful=true)` as hard set to 1.0
  - [x] 1.4 Migration `009_quality.sql`: add `via TEXT NOT NULL DEFAULT 'recall'` to `session_accesses`; add `fragment_tombstones` table and `AFTER DELETE` trigger on `fragments`
  - [x] 1.5 Inject path (rules file and hook payload) records `session_accesses` rows with `via='inject'` for the current session
  - [x] 1.6 `boost.apply` skips fragments that have `via='inject'` in the same session
  - [x] 1.7 `ranking.top_n` tie-break: `(-score, last_accessed_at desc, not pinned)`
  - [x] 1.8 `_render_ledger_as_fragment`: goal, decisions, blockers, last 5 done; drop asks and any line containing `<`; hard cap 4000 chars
  - [x] 1.9 `fragments.create`: drop tags matching `log_progress_auto:*`, `trigger:*`, `client:*`
  - [x] 1.10 Decay: log a feedback row only for fragments whose confidence changed, plus one `decay-cycle` summary row
  - [x] 1.11 Add `feedback.prune(days=90)` and `maintenance.cleanup_sessions` age filter of 24 h; call both plus `reindex` from the archive job template
  - [x] 1.12 MCP tool `mark(fragment_id, useful: bool)`; register in `server.py` with annotations like the existing tools
  - [x] 1.13 `hippo audit`: print the PRD section 1 table plus thresholds; exit 1 on breach
  - [x] 1.14 `hippo purge-noise [--dry-run]`: backup, delete fragments matching 1.1 patterns, re-render oversized session summaries from their session when it still exists, else cut at 4000 chars
  - [x] 1.15 Tests: `test_autoremember_filters.py`, `test_boost_dynamics.py`, `test_distill_cap.py`, `test_audit.py`
  - [x] 1.16 Run `purge-noise --dry-run` then for real on the fixture copy; confirm `hippo audit` reports 0 noise fragments
  - [x] 1.17 Re-run `hippo install-hooks` and reload launchd agents; verify a Monitor notification in a live Claude Code session creates no fragment
  - [x] 1.18 Web UI triage view: `/triage` route sorted by `accessed` desc, columns confidence, pinned, project, size, age; row actions unpin, forget, mark not useful calling the same functions as the MCP tools

- [x] 2.0 Phase 2 — Project scoping
  - [x] 2.1 Migration `010_projects.sql`: `fragments.project TEXT`, `sessions.project TEXT`, `idx_fragments_project`
  - [x] 2.2 `projects.py`: load and validate `projects.json`; `match_remote(url)`, `match_path(path)`, `resolve(cwd) -> str | None` following FR14 order; git remote read via `git -C <cwd> remote get-url origin` with a 1 s timeout and cached per process
  - [x] 2.3 `sessions.start`: resolve and store `project`; `_detect_workspace` result is the cwd for GUI clients
  - [x] 2.4 `remember`, `autoremember`, `end_progress`, idle auto-distill: stamp `project` from the current session; `remember(scope="global")` stores `NULL`
  - [x] 2.5 `fragments.search_fts`, `list_all`, and `embeddings.search.semantic_topk`: accept `project` filter applied to the candidate set before ranking
  - [x] 2.6 `recall(scope="project"|"all")` default `project`; filter is `project = ? OR project IS NULL`
  - [x] 2.7 Split injection: rules-file block uses `top_n(project=None, global_only=True)`; hook payload uses `top_n(project=current)` with `hook_fragment_limit`
  - [x] 2.8 Hook templates pass `HIPPOCAMPUS_CWD` to `hippo context` so the payload resolves the project; Cursor and VS Code hooks pass the workspace root
  - [x] 2.9 CLI `hippo project list|add|detect|assign`
  - [x] 2.10 `hippo project backfill [--dry-run]`: session_key cwd match, then tag match against project names and aliases; print counts per project and unmatched
  - [x] 2.11 Web UI: project column and filter on the fragment list
  - [x] 2.12 Tests: `test_projects.py` (remote wins over path, ancestor marker file, env override, unmatched is None), `test_recall_scope.py` (hidden project not returned, `scope="all"` returns it, global always returned, semantic k not starved)
  - [x] 2.13 Draft `projects.json` for the owner's 4 to 5 projects from the audit's tag list; owner confirms names and repos
  - [ ] 2.14 Run backfill on the fixture copy; confirm > 80 % assigned; then run on the live primary DB after `hippo backup`

- [x] 3.0 Phase 3 — Device sync via self-hosted endpoint
  - [x] 3.1 `sync/merge.py`: `merge_fragment(local, remote) -> merged`, `apply_tombstone(local, tombstone) -> bool`, `merge_association(a, b)`; pure functions over dicts per FR28
  - [x] 3.2 Migration `011_sync.sql`: `sync_state` table
  - [x] 3.3 `sync/client.py`: device id file (ULID, mode 0600), watermark from `max(updated_at, last_accessed_at)`, `collect_ops()` producing fragment, association, tombstone, and config ops
  - [x] 3.4 `sync/client.py`: `push()` in batches of 200 with `httpx`; `pull()` loops until `next_seq` catches up; apply through `merge.py` inside short transactions; advance `last_pull_seq` per batch
  - [x] 3.5 Embedding handling on pull: accept blob if model name matches the local setting, else drop and leave to reindex
  - [x] 3.6 `sync/server.py`: FastAPI app, bearer token middleware, `sync.db` oplog, endpoints from FR22; `pull` excludes the caller's own device ops
  - [x] 3.7 CLI `hippo sync [--quiet]`, `hippo sync serve [--port]`, `hippo sync status`; settings `sync_url`, `sync_token`, `sync_enabled`
  - [x] 3.8 Inject launchd and cron templates append `&& hippo sync --quiet` only when `sync_enabled`; `--quiet` exits 0 on network error and records `last_error`
  - [x] 3.9 `hippo doctor` and `hippo audit` show device id, server, last ok sync, pending ops, last error, lag
  - [x] 3.10 `scripts/install.sh --sync-server`: launchd plist on macOS, systemd unit on Linux, token generated into `~/.hippocampus/config.json` mode 0600
  - [x] 3.11 Tests: `test_sync_merge.py` (each FR28 rule, commutativity of merge for two devices, tombstone vs newer update), `test_sync_roundtrip.py` (device A remembers, B pulls; B pins, A sees pin; A forgets, B deletes; offline push retries)
  - [x] 3.12 Verify hot path: `grep` that no module under `mcp/`, `web/`, or hooks imports `sync.client`; re-measure `recall` p95 against baseline

- [ ] 4.0 Phase 4 — Rollout and docs
  - [x] 4.1 Choose the server host (desktop over Tailscale or VPS); install with `install.sh --sync-server`; confirm `GET /v1/health` from a second device
  - [x] 4.2 Enroll the primary device first: `purge-noise`, `project backfill`, `hippo config set sync_url/sync_token/sync_enabled`, `hippo sync`; confirm op count on the server equals fragment count
  - [ ] 4.3 Enroll each remaining device: same steps; after first pull run `hippo audit` and spot-check 5 fragments exist on both sides
  - [ ] 4.4 Open a session in one project repo on two devices; confirm the hook payload shows only that project plus global fragments
  - [x] 4.5 RUNBOOK: server install, enrollment, first sync from richest DB, recovery from a bad merge (`hippo restore` + `sync_state` reset)
  - [x] 4.6 README section "Multiple devices and projects"; CHANGELOG `1.8.0` with before and after numbers from 0.3 and 3.12
  - [ ] 4.7 Update `plans/README.md` row for v11
