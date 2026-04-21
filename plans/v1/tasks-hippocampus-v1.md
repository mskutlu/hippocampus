# Tasks — Hippocampus V1

## Relevant Files

- `pyproject.toml` — Python project config (uv/PEP 621, deps: mcp, ulid-py, pyyaml, click, pytest).
- `src/hippocampus/__init__.py` — Package root, version.
- `src/hippocampus/config.py` — Paths, ranking constants, defaults.
- `migrations/001_initial.sql` — Schema: fragments, tags, associations, sessions, session_accesses, feedback_log, FTS5.
- `src/hippocampus/storage/db.py` — Connection, WAL mode, migration runner.
- `src/hippocampus/storage/fragments.py` — CRUD for fragments + tags.
- `src/hippocampus/storage/associations.py` — Co-access edge table.
- `src/hippocampus/storage/sessions.py` — Session open/close + access log.
- `src/hippocampus/storage/feedback.py` — Negative feedback log.
- `src/hippocampus/dynamics/boost.py` — Access boost (+0.015), context tag, association strengthening.
- `src/hippocampus/dynamics/decay.py` — Per-session decay (-0.002), shield.
- `src/hippocampus/dynamics/ranking.py` — `confidence * 0.7 + recency * 0.3` scoring.
- `src/hippocampus/dynamics/archive.py` — Auto-prune at confidence < 0.05 for 7+ days.
- `src/hippocampus/sync/obsidian_mirror.py` — Write fragment → `~/hippocampus-vault/Fragments/<id>.md`.
- `src/hippocampus/mcp/server.py` — stdio MCP server + tool registrations.
- `src/hippocampus/mcp/tools.py` — Tool implementations (recall, remember, forget, pin, ...).
- `src/hippocampus/clients/injector.py` — Core marker-delimited block upsert logic.
- `src/hippocampus/clients/devin.py` — Devin AGENTS.md injection adapter.
- `src/hippocampus/clients/claude_code.py` — Claude CLAUDE.md injection adapter.
- `src/hippocampus/clients/opencode.py` — OpenCode injection adapter.
- `src/hippocampus/clients/windsurf.py` — Windsurf rules injection adapter.
- `src/hippocampus/clients/antigravity.py` — Antigravity injection adapter.
- `src/hippocampus/cli/main.py` — `hippo` CLI (click-based).
- `scripts/install.sh` — One-shot installer (DB init, launchd, client wiring, doctor).
- `scripts/com.hippocampus.daemon.plist.template` — Launchd agent template.
- `tests/unit/test_dynamics.py` — Boost/decay/shield math.
- `tests/unit/test_ranking.py` — Score formula.
- `tests/unit/test_storage.py` — CRUD + WAL concurrency.
- `tests/unit/test_injector.py` — Marker-block upsert correctness.
- `tests/integration/test_mcp_tools.py` — Full MCP tool flow via real server.
- `tests/integration/test_obsidian_mirror.py` — DB ↔ vault sync round-trip.
- `tests/e2e/test_full_cycle.py` — remember → recall (boost) → new session (shield) → unused (decay) → archive.
- `docs/README.md` — Install, first-use, troubleshooting.
- `docs/ARCHITECTURE.md` — Data flow, schema, MCP surface, injection pipeline.
- `docs/RUNBOOK.md` — Daemon management, DB backup/restore, debugging.

### Notes

- Use `uv` for venv + deps: `uv sync` to install, `uv run pytest` to test.
- SQLite file: `~/.hippocampus/hippocampus.db`. Tests use a temp DB via `pytest` fixtures.
- All datetimes are UTC ISO-8601.
- Logging to `~/.hippocampus/logs/*.log`; also stderr for MCP (never stdout — stdio protocol uses it).

## Instructions for Completing Tasks

As each sub-task is completed, update `- [ ]` → `- [x]` in this file to track progress. Update after each sub-task, not only after a parent finishes.

## Tasks

- [x] 0.0 Create feature branch
  - [x] 0.1 `cd ~/IdeaProjects/hippocampus && git init && git checkout -b feature/hippocampus-v1`

- [x] 1.0 Project skeleton & dependencies
  - [x] 1.1 Create `pyproject.toml` with Python 3.11+, deps (mcp, ulid-py, pyyaml, click, pytest, pytest-asyncio)
  - [x] 1.2 Create `src/hippocampus/__init__.py` with `__version__ = "0.1.0"`
  - [x] 1.3 Create `src/hippocampus/config.py` with paths (`HOME`, `HIPPOCAMPUS_HOME`, `VAULT_HOME`, `DB_PATH`, `LOG_DIR`, `BACKUPS_DIR`) and constants (`BOOST_DELTA=0.015`, `DECAY_DELTA=0.002`, `FEEDBACK_DELTA=0.02`, `CONFIDENCE_INIT=0.5`, `ARCHIVE_THRESHOLD=0.05`, `ARCHIVE_GRACE_DAYS=7`, `RECENCY_HALFLIFE_DAYS=14`, `RANK_W_CONF=0.7`, `RANK_W_RECENCY=0.3`, `TOP_N_DEFAULT=15`)
  - [x] 1.4 Add `.gitignore` (pycache, .venv, *.db, *.log, .DS_Store)
  - [x] 1.5 Create `README.md` stub with install + quickstart
  - [x] 1.6 Run `uv sync` and confirm all deps install cleanly

- [x] 2.0 Storage layer (SQLite + migrations)
  - [x] 2.1 Write `migrations/001_initial.sql` — all tables, indexes, FTS5 virtual table + triggers
  - [x] 2.2 Implement `storage/db.py`: `connect()`, `init_db()`, migration runner (tracks applied migrations in `schema_migrations`), WAL + foreign keys pragmas
  - [x] 2.3 Implement `storage/fragments.py`: `create()`, `get()`, `update()`, `delete()`, `search_fts()`, `list_by_tag()`, `list_all()`
  - [x] 2.4 Implement `storage/associations.py`: `strengthen(a, b)`, `get_associated(id, limit=10)`, `decay_weights()` (optional for V1)
  - [x] 2.5 Implement `storage/sessions.py`: `open_session(client)`, `close_session(id)`, `log_access(session_id, fragment_id)`, `get_current_session_id(client)`, `auto_close_stale()`
  - [x] 2.6 Implement `storage/feedback.py`: `log_feedback(fragment_id, kind, delta, reason)`
  - [x] 2.7 Unit tests: CRUD round-trip, FTS search ranking, WAL concurrency (2 threads), auto-close stale sessions

- [x] 3.0 Biological dynamics
  - [x] 3.1 Implement `dynamics/boost.py`: `boost_on_access(fragment_id, context_tag?)` → update confidence (+0.015 cap 1.0), accessed+=1, last_accessed_at=now, add tag, log access
  - [x] 3.2 Implement `dynamics/boost.py:strengthen_associations(fragment_ids)` — pairwise iterate, call `associations.strengthen`
  - [x] 3.3 Implement `dynamics/decay.py:run_decay_cycle()` → for each fragment where NOT pinned AND NOT accessed in (current_session OR previous_session): confidence -= 0.002 (floor 0.0)
  - [x] 3.4 Implement `dynamics/ranking.py:compute_score(confidence, last_accessed_at, now)` → `confidence * 0.7 + recency * 0.3` where recency = `exp(-days/14)`
  - [x] 3.5 Implement `dynamics/ranking.py:top_n(limit=15)` → ordered list for injection
  - [x] 3.6 Implement `dynamics/archive.py:archive_low_confidence()` → fragments below 0.05 for 7+ days → move to archive dir, delete from DB
  - [x] 3.7 Unit tests: boost math (saturation at 1.0), decay floor (0.0), shield (recently-accessed not decayed), pin never decays, ranking order, archive threshold
  - [x] 3.8 Unit test: negative feedback (`forget` → -0.02, floored at 0.0, logged)

- [x] 4.0 Obsidian mirror sync
  - [x] 4.1 Implement `sync/obsidian_mirror.py:write_fragment(fragment)` → writes `~/hippocampus-vault/Fragments/<id>.md` with YAML frontmatter + `# Summary` + `# Content` sections
  - [x] 4.2 Implement `sync/obsidian_mirror.py:delete_fragment_mirror(id)`
  - [x] 4.3 Implement `sync/obsidian_mirror.py:archive_fragment_mirror(id)` — moves to `Fragments/.archive/`
  - [x] 4.4 Wire mirror write into every `storage/fragments.py:create()` and `update()` (hook via storage module, not forced in the caller)
  - [x] 4.5 Integration test: create fragment → mirror file exists with matching frontmatter → update fragment → mirror updated → delete → mirror gone

- [x] 5.0 MCP server
  - [x] 5.1 Implement `mcp/server.py` — stdio MCP server, lists tools via official SDK
  - [x] 5.2 Implement `mcp/tools.py:recall(query, limit, min_confidence, context_tag)` — FTS search + rank + boost each hit + strengthen co-access + log session access → return list of `{id, summary, confidence, tags}`
  - [x] 5.3 Implement `mcp/tools.py:remember(content, summary?, tags?, source_type?, source_ref?)` → create fragment, mirror it, return id
  - [x] 5.4 Implement `mcp/tools.py:forget(fragment_id, reason?)` → confidence -= 0.02, log feedback
  - [x] 5.5 Implement `mcp/tools.py:pin` / `unpin`
  - [x] 5.6 Implement `mcp/tools.py:get_fragment(id)` → read + boost
  - [x] 5.7 Implement `mcp/tools.py:list_fragments(tag?, min_confidence?, limit)` — no boost
  - [x] 5.8 Implement `mcp/tools.py:top_fragments(limit)` — no boost (used by injector)
  - [x] 5.9 Implement `mcp/tools.py:get_stats()` → count, avg confidence, most recent, pinned count, archived count
  - [x] 5.10 Ensure all MCP output goes via JSON-RPC on stdout; all logs go to stderr + log files
  - [x] 5.11 Integration test: spawn MCP server as subprocess, exercise all 8 tools end-to-end
  - [x] 5.12 Add `scripts/run_mcp.sh` launcher (handles virtualenv activation)

- [x] 6.0 Session-start hook + top-N injection generator
  - [x] 6.1 Implement CLI `hippo session start --client <name>` → opens session, writes session id to `~/.hippocampus/current-session-<name>.txt`, triggers `hippo inject`
  - [x] 6.2 Implement CLI `hippo session end --client <name>` → closes session
  - [x] 6.3 Implement CLI `hippo inject` → compute top-N, format injection block, run all registered client adapters
  - [x] 6.4 Implement `clients/injector.py:upsert_block(path, content, marker_start, marker_end)` — atomic, idempotent, skip-if-unchanged
  - [x] 6.5 Implement `clients/injector.py:format_injection_block(fragments, limit=15)` — formatted markdown (header, bullet list with id, confidence, summary)
  - [x] 6.6 Unit tests: block upsert (no block → add; existing block → replace; skip if identical)

- [x] 7.0 Wire auto-injection into 5 AI clients
  - [x] 7.1 Implement `clients/devin.py` → upsert into `~/.config/devin/AGENTS.md`
  - [x] 7.2 Implement `clients/claude_code.py` → upsert into `~/.claude/CLAUDE.md`
  - [x] 7.3 Implement `clients/opencode.py` → upsert into `~/.opencode/AGENTS.md` (create file if missing)
  - [x] 7.4 Implement `clients/windsurf.py` → upsert into `~/.windsurf/rules/hippocampus.md` (create always-on rule with `trigger: always_on` frontmatter)
  - [x] 7.5 Implement `clients/antigravity.py` → same pattern (path TBD on first install; use `hippo doctor` to prompt user)
  - [x] 7.6 Implement CLI `hippo register --client <name>` to add the Hippocampus MCP server entry into the client's MCP config
  - [x] 7.7 Integration test: `hippo inject` writes block into 5 fake client paths (tmpdir), verify each file

- [x] 8.0 Decay daemon (launchd)
  - [x] 8.1 Create `scripts/com.hippocampus.daemon.plist.template` (ProgramArguments: `uv run hippo decay`; StartInterval: 3600; StandardOutPath/ErrorPath → log files)
  - [x] 8.2 Add a second plist for `hippo inject` every 600s and `hippo archive` every 86400s
  - [x] 8.3 Implement CLI `hippo decay [--dry-run]` → runs `dynamics.decay.run_decay_cycle()`
  - [x] 8.4 Implement CLI `hippo archive [--dry-run]`
  - [x] 8.5 Add `install.sh` steps: render plist templates with real paths, `launchctl load`, confirm running
  - [x] 8.6 Add `hippo doctor` check: daemon status via `launchctl print`

- [x] 9.0 Testing
  - [x] 9.1 Configure `pytest` with fixtures: temp DB, temp vault, mock launchd
  - [x] 9.2 Unit tests pass (all in `tests/unit/`) — 100% coverage on `dynamics/` and `storage/`
  - [x] 9.3 Integration tests pass (`tests/integration/`) — MCP server + mirror sync
  - [x] 9.4 E2E test `tests/e2e/test_full_cycle.py`: remember → recall (boost check) → open new session → don't recall (shield check — no decay) → two more sessions with no access (decay check) → confidence drops below threshold → archive works
  - [x] 9.5 Add CI-friendly `make test` (or `uv run pytest`) as the canonical runner

- [x] 10.0 Install + bootstrap + docs
  - [x] 10.1 Write `scripts/install.sh`: create `~/.hippocampus/` dirs, `uv sync`, run migrations, register launchd, run `hippo register` for each client, run `hippo doctor`
  - [x] 10.2 Write `docs/README.md` with install/usage/troubleshooting
  - [x] 10.3 Write `docs/ARCHITECTURE.md` with diagrams (data flow, injection pipeline, schema ERD)
  - [x] 10.4 Write `docs/RUNBOOK.md`: backup/restore, daemon control, debugging, "my confidences look weird" playbook
  - [x] 10.5 Final commit, tag `v0.1.0`
