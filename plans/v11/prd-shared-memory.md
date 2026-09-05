# PRD V11 — Shared memory: quality, project scoping, device sync

> Status: drafted
> Author: @msk
> Target version: `1.8.0`
> Replaces: nothing. Builds on v9 (passive autonomy) and v10 (wiki).
> Tasks: [tasks-shared-memory.md](./tasks-shared-memory.md)

## 1. Introduction

Hippocampus runs on 4 devices, each with its own `~/.hippocampus/hippocampus.db`.
Nothing moves between them, so a lesson learned on the laptop is invisible on
the desktop. Every device also mixes 4 to 5 companies' work into one ranking,
so a session in one company's repo is injected with summaries from another.

An audit of the current database (2026-09-05, 1308 fragments, 152 MB) found that
the mechanics that decide what gets injected have degraded:

| Finding | Measurement |
|---|---|
| Hook noise stored as pinned rules | 78 fragments contain `<task-notification>`; 5 of the 15 injected fragments are such payloads |
| Confidence saturated | 505 of 1308 fragments at exactly 1.0; ranking degrades to raw access count |
| Injection feedback loop | Top fragment has 1702 accesses; 893 distinct fragments got any boost in 7 days out of 15,861 boosts |
| Session summaries are raw dumps | 804 session-summary fragments, largest 97 KB, average fragment 2.1 KB, no cap |
| Tags carry no signal | 1251 distinct tags for 1308 fragments, mostly `log_progress_auto:*` |
| Dead sessions | 60,659 of 62,913 sessions have no ledger entry; sessions + feedback_log are 40 MB |
| Embedding gaps | 139 fragments have no vector and are invisible to semantic recall |

The goal of V11 is one shared long-term memory across all devices, partitioned
by project, fed by clean data, with zero added latency on the MCP request path.

Order matters: quality fixes ship first so sync does not replicate garbage,
project scoping second so synced rows already carry a project, sync last.

## 2. Goals

| ID | Goal |
|----|------|
| G1 | No hook, system, or task-notification text is ever stored as a fragment. |
| G2 | Confidence distribution is no longer saturated; injection set changes as work changes. |
| G3 | Every fragment and session carries a `project` (or is explicitly global). |
| G4 | Inside a project, recall and injection show that project plus global rules only. |
| G5 | Long-term memory converges across devices within 10 minutes of a change, offline-tolerant. |
| G6 | Sync adds no code to any MCP tool call path. Hot-path latency is unchanged. |
| G7 | Database size and background write volume drop by at least half. |

## 3. User Stories

- As the owner, when I `cd` into a acme repo on any device, the injected
  memory shows acme fragments and my global rules, nothing from other companies.
- As the owner, when I `remember` something on the laptop, it is available in
  recall on the desktop the next time I open a session there.
- As the owner, when I `forget` or `pin` a fragment on one device, that decision
  reaches the others and is not undone by a stale copy.
- As the owner, when a background Monitor task posts a notification into my
  terminal, nothing is auto-remembered from it.
- As the owner, when I close a session, the distilled fragment is a few
  paragraphs of decisions and outcomes, not the raw transcript.
- As the owner, I can run one command that tells me whether memory is healthy:
  noise ratio, saturation, sync lag, embedding coverage.
- As the owner, I can ask for memory from all projects explicitly when I need it.

## 4. Functional Requirements

### 4.1 Quality (Phase 1)

1. `autoremember` must skip any prompt whose stripped text starts with `<`, or
   contains `<task-notification>`, `<system-reminder>`, or `<task-id>`.
   The skip list is a setting `autoremember_ignore_patterns` (list of regex).
2. `autoremember` must only fire when the trigger sentence is at most 300
   characters and contains no `<...>` tag.
3. Boost must be asymptotic: `delta = BOOST_DELTA * (1 - confidence)`. A
   fragment at 0.95 gains at most 0.00075 per access. Pin and explicit
   `mark(useful=true)` may still set confidence to 1.0.
4. Recall must not boost a fragment that was already injected into the current
   session. `session_accesses` gains a `via` column (`inject` or `recall`); the
   inject path records its ids; boost skips ids with `via='inject'` for the
   same session.
5. `top_n` must break ties by `last_accessed_at`, not by `accessed`, so that
   old high-count fragments do not stay locked in.
6. `_render_ledger_as_fragment` must cap content at 4000 characters and
   include, in order: goal, decisions, blockers, the last 5 `done` entries.
   `ask` entries and any line containing `<` are excluded.
7. Fragments must no longer receive pipeline tags. Tags matching
   `log_progress_auto:*`, `trigger:*`, `client:*` are dropped at write time.
   The client name stays as one plain tag.
8. The daily archive job must also run: `cleanup-sessions` (delete sessions
   with no ledger entry older than 24 h), `reindex` (embed fragments without a
   vector), and `feedback_log` pruning (rows older than 90 days).
9. The decay cycle must write one `feedback_log` row per fragment that actually
   changed, and one summary row per cycle. It must not log shielded or pinned
   fragments.
10. A new MCP tool `mark(fragment_id, useful: bool)` must apply a boost to 1.0
    for `useful=true` and the existing negative delta for `useful=false`.
11. A new CLI command `hippo audit` must print the table in section 1 for the
    current database, plus sync lag once Phase 3 lands. Exit code 1 if any
    threshold in section 7 is breached.
12a. The web UI must gain a triage view: fragments sorted by `accessed`
    descending with columns confidence, pinned, project, size, and age, and
    one-click `unpin`, `forget`, and `mark not useful` actions. Row actions
    call the existing MCP tool functions so behaviour matches the tools.
12. A one-time command `hippo purge-noise [--dry-run]` must delete fragments
    matching requirement 1's patterns, truncate session-summary fragments over
    4000 characters using requirement 6's renderer where the source session
    still exists, and otherwise cut at 4000 characters. It must take a backup
    first.

### 4.2 Project scoping (Phase 2)

13. A new file `~/.hippocampus/projects.json` must define projects. Schema:

    ```json
    {
      "acme": {
        "remotes": ["github.com/acme/*", "gitlab.com/acme/*"],
        "paths": ["~/work/acme-*", "~/work/acme-legacy-*", "~/work/acme-gateway"]
      },
      "qa-suite": { "remotes": ["github.com/globex/qa-*"], "paths": ["~/work/globex/**"] }
    }
    ```

    Remotes match the `origin` URL with scheme and `.git` stripped. Paths are
    globs expanded per device. A remote match wins over a path match.
14. Project resolution order for a session: `HIPPOCAMPUS_PROJECT` env, then
    `.hippocampus-project` file in the cwd or any ancestor, then
    `projects.json` remote rule, then `projects.json` path rule, then `null`
    (global).
15. `sessions` gains a `project TEXT NULL` column, set at session start from
    requirement 14. `fragments` gains `project TEXT NULL` with an index.
16. `remember`, `autoremember`, `end_progress` distillation, and idle
    auto-distill must stamp the current session's project on the fragment.
    `remember` accepts `scope="global"` to store with `project=NULL`.
17. `recall` must default to `WHERE project = :current OR project IS NULL`.
    `recall(scope="all")` removes the filter. Semantic top-k must apply the
    same filter before ranking so the k slots are not consumed by hidden rows.
18. Injection must split: the always-on rules file block carries global
    fragments only (`project IS NULL`), and the hook `additionalContext`
    carries the current project's top fragments. `hook_fragment_limit` applies
    to the project set.
19. New CLI group `hippo project`: `list`, `add <name> --remote <glob>
    --path <glob>`, `detect [path]` (prints the resolved project for a path),
    `assign <fragment_id|--tag tag|--session-prefix p> <project>`.
20. Backfill: `hippo project backfill [--dry-run]` must assign a project to
    existing fragments by, in order: source session's `session_key` cwd
    component matched against path rules, then fragment tags matched against
    project names and their aliases. Unmatched fragments stay global.
21. The web UI fragment list must show and filter by project.

### 4.3 Device sync via self-hosted endpoint (Phase 3)

22. A new module `hippocampus.sync.server` must expose a FastAPI app with:
    - `GET /v1/health`
    - `POST /v1/push` body `{device, ops: [...]}`, returns `{accepted, seq}`
    - `GET /v1/pull?since=<seq>&device=<id>`, returns ops from other devices
      with `seq > since`, capped at 500 per call, plus `next_seq`.
    - `GET /v1/config` and `PUT /v1/config` for `projects.json`.
    Auth is a static bearer token read from `HIPPOCAMPUS_SYNC_TOKEN`. Requests
    without it get 401. The app reuses the `web` optional dependency group.
23. The server must store ops in its own SQLite file `sync.db` with a table
    `ops(seq INTEGER PRIMARY KEY, device TEXT, entity TEXT, entity_id TEXT,
    op TEXT, payload TEXT, updated_at TEXT, received_at TEXT)`. It keeps the
    full oplog; compaction is out of scope for V11.
24. `hippo sync serve [--port 7879]` must run the server. `scripts/install.sh`
    gains `--sync-server` to install it as a launchd or systemd service.
    Documentation must recommend running it over Tailscale or behind a
    reverse proxy with TLS; the server itself speaks plain HTTP.
25. Each device must have a ULID `device_id` at `~/.hippocampus/device_id`,
    created on first `hippo sync`.
26. `hippo sync [--quiet]` must push then pull. Push sends fragments whose
    `updated_at` or `last_accessed_at` is newer than the stored watermark,
    plus tombstones. Pull applies ops from other devices. Both run in batches
    of 200 rows inside short transactions with the existing 5 s busy timeout.
27. Synced entities: `fragment` (row, tags, embedding blob with model name),
    `association` (pair, weight, count), `tombstone` (forget), and `config`
    (`projects.json`). Not synced: sessions, ledger, feedback_log,
    transcripts, handoffs, wiki tables, backups.
28. Merge rules per fragment id, applied on pull:
    - `content`, `summary`, `project`, `source_*`: newer `updated_at` wins.
    - `pinned`: logical OR, unless the newer op is an explicit unpin.
    - `accessed`: max. `last_accessed_at`: max. `confidence`: max.
    - `below_threshold_since`: min non-null; cleared if confidence recovers.
    - tags: set union. associations: union, max `weight`, max `co_accessed_count`.
    - tombstone: deletes locally unless a local update is newer than the tombstone.
    - embedding: accept if model name matches the local setting, else drop and
      let the daily reindex recompute.
29. A local table `sync_state(device_id, last_push_watermark, last_pull_seq,
    last_ok_at, last_error)` and a `fragment_tombstones(fragment_id,
    deleted_at)` table filled by a trigger on `fragments` delete.
30. Sync must never be invoked from an MCP tool, a hook, or the web server. It
    runs only from the 10-minute inject launchd job (`hippo inject && hippo
    sync --quiet`) or manually. Network failure exits 0 with `--quiet` and is
    reported by `hippo audit` and `hippo doctor` as sync lag.
31. `hippo doctor` must show: device id, server URL, last successful sync,
    pending ops, and last error.
32. Client config settings: `sync_url`, `sync_token` (stored in
    `~/.hippocampus/config.json`, file mode 0600), `sync_enabled` (default
    false).

### 4.4 Rollout (Phase 4)

33. Runbook section for: starting the server on the always-on machine,
    enrolling each device (`hippo config set sync_url ...`, `hippo sync`),
    first-sync from the richest database, verifying with `hippo audit`.
34. First enrollment of a device with an existing database must run `hippo
    purge-noise` and `hippo project backfill` before its first push.
35. CHANGELOG entry and README section "Multiple devices and projects" with
    these steps in order: start the server with `install.sh --sync-server`
    and note the printed token; on each device `hippo config set sync_url`,
    `sync_token`, `sync_enabled true`; on a device with an existing database
    run `hippo purge-noise` and `hippo project backfill` once; run `hippo sync`
    by hand and confirm `hippo doctor` shows a successful sync; from then on
    the 10-minute inject job syncs automatically. The section also shows a
    minimal `projects.json` and the `hippo project detect` check.

## 5. Non-Goals

- Syncing working memory, handoffs, transcripts, or wiki pages. Working memory
  is keyed by terminal and workspace by design; handoffs resume per device.
- Live or real-time sync. Ten-minute convergence is the target.
- Multi-user or per-user access control on the sync server. One token, one owner.
- Oplog compaction or server-side ranking. The server is a dumb log.
- Replacing SQLite with a network database.
- Cross-project semantic linking. `scope="all"` is the escape hatch.
- Windows-native service installation for the sync server.

## 6. Design Considerations

- The always-on rules file block becomes small and stable (global rules only).
  Project memory arrives through the hook payload, which already knows the
  cwd. This is why the split in requirement 18 costs nothing new.
- Web UI: one project filter dropdown and a project column. No new pages.

## 7. Technical Considerations

- Migrations: `009_quality.sql` (session_accesses.via, tombstones trigger),
  `010_projects.sql` (project columns, index), `011_sync.sql` (sync_state).
  Existing migration runner backs up before applying.
- All merge logic must be pure functions over dicts so it can be unit tested
  without a server. Server and client share one `merge.py`.
- `accessed` merges as max, not sum, so two devices recalling the same fragment
  do not double count. Decay stays local; because `last_accessed_at` syncs,
  the 30-day recency shield agrees across devices.
- Semantic recall with a project filter: run FTS and cosine over the filtered
  candidate set, not the full table then filter, otherwise `k` is starved.
- Push watermark uses `max(updated_at, last_accessed_at)` because recall boosts
  touch only `last_accessed_at` and `accessed`.
- `hippo audit` thresholds (exit 1 if breached): noise fragments > 0,
  fragments at 1.0 > 30 %, embedding coverage < 98 %, sessions without ledger
  older than 24 h > 100, sync lag > 60 min when `sync_enabled`.
- Existing `session_key` for terminal clients embeds cwd; the backfill parses
  it. GUI-client sessions carry the workspace path in the same field.
- Ponytail: no per-project databases, no ORM, no queue. One SQLite oplog, one
  bearer token, one cron tick.

## 8. Success Metrics

| Metric | Now | Target |
|---|---|---|
| Fragments containing hook markup | 78 | 0 |
| Fragments at confidence 1.0 | 39 % | < 20 % after 30 days |
| Distinct fragments boosted per week | 893 | > 1500, or > 60 % of live fragments |
| Median session-summary fragment size | ~2 KB, max 97 KB | ≤ 4 KB max |
| Fragments with a project | 0 % | > 80 % after backfill |
| Cross-project fragments injected in a project session | uncontrolled | 0 |
| Convergence time for a `remember` across devices | never | ≤ 10 min online |
| MCP `recall` p95 latency | baseline to be measured in Phase 1 | unchanged ± 5 % |
| Database size | 152 MB | < 80 MB |
| Rows written per decay cycle | ~1300 | < 100 typical |

## 9. Open Questions

1. Which machine hosts the sync server: the desktop, or a small VPS? Tailscale
   makes either safe; a VPS avoids depending on one device being awake.
2. Should `pinned` global rules from `~/.claude/CLAUDE.md`-style content be
   imported as global fragments, or stay in the rules files only?
3. Do you want `hippo audit` wired into `hippo doctor` so the install check
   fails on bad memory health, or kept separate?
4. Project aliases for backfill: the audit shows tags like `devin`, `acme`,
   `orders-service`, `qa-suite`. A first draft of `projects.json` needs
   your confirmation of the 4 to 5 project names and their repos.
