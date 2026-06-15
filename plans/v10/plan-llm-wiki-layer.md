# V10 Plan — LLM Wiki layer

PRD: [prd-llm-wiki-layer.md](./prd-llm-wiki-layer.md)

## 1. Product Decision

Build the LLM Wiki layer as an **additive, database-backed markdown knowledge
base workflow** on top of Hippocampus.

Hippocampus remains responsible for:

- Cross-client long-term memory fragments.
- Working-memory ledgers.
- Transcript provenance.
- Recall, confidence, boost/decay, injection, and MCP registration.

The new wiki layer is responsible for:

- Project-scoped wiki initialization.
- Raw source registration.
- Generated markdown page records.
- Database-backed index and chronological log maintenance.
- Source-backed synthesis.
- Wiki lint and repair planning.
- Filing valuable query answers back into the knowledge base.
- Optional markdown materialization/export for Obsidian and git.

This keeps the existing memory system stable while giving users the more
structured "LLM Wiki" workflow.

## 2. Canonical Storage Decision

The database is canonical for V10 wiki pages.

Markdown files are still important, but they are not the first thing agents
should inspect. Agents should call Hippocampus wiki tools, which read from the
database and return the current project wiki state. Markdown files are
materialized only after a wiki project is initialized, or when the user runs an
export/sync workflow.

Why:

- Agents can access wiki pages directly through MCP without guessing where a
  project stores docs.
- Project state can be validated before work begins.
- `wiki_log` and `wiki_index` can be queried structurally instead of parsed from
  text every time.
- Obsidian remains supported through exported markdown.

Required gate:

If a project has no `wiki_projects` row, wiki operations must stop and return:

```text
This project does not have an initialized Hippocampus wiki yet.
Run: hippo wiki init --project <name>
```

Do not let agents create ad hoc `index.md` or `log.md` files while doing another
operation. The flow is: initialize/create wiki files first, then ingest/query.

## 3. Materialized Layout

Default root:

```text
~/hippocampus-vault/Wiki/
├── raw/
│   ├── inbox/
│   └── assets/
├── wiki/
│   ├── index.md
│   ├── log.md
│   ├── overview.md
│   ├── sources/
│   ├── entities/
│   ├── concepts/
│   ├── topics/
│   └── analyses/
└── schema/
    └── LLM-WIKI.md
```

Rationale:

- `~/hippocampus-vault/Fragments/` remains reserved for the current fragment
  mirror.
- `Wiki/raw/` contains source files or pointers exported from the database.
- `Wiki/wiki/` contains materialized generated knowledge pages.
- `Wiki/schema/LLM-WIKI.md` is the operating guide agents read before editing.

## 4. Architecture

### 4.1 Migration

Add `migrations/006_wiki.sql`:

| Table | Purpose |
|-------|---------|
| `wiki_projects` | One row per initialized project/wiki context. |
| `wiki_sources` | Raw source records, hashes, paths, titles, source types. |
| `wiki_pages` | Canonical markdown bodies and metadata for source/entity/concept/topic/analysis/index pages. |
| `wiki_page_sources` | Many-to-many citation/source relationship. |
| `wiki_links` | Extracted wikilink graph for backlinks/orphan checks. |
| `wiki_log` | Chronological append-only operation log. |

Suggested schema shape:

```sql
CREATE TABLE wiki_projects (
  id TEXT PRIMARY KEY,
  project_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  workspace_path TEXT,
  export_root TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE wiki_pages (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES wiki_projects(id) ON DELETE CASCADE,
  page_type TEXT NOT NULL,
  title TEXT NOT NULL,
  normalized_title TEXT NOT NULL,
  path TEXT NOT NULL,
  markdown TEXT NOT NULL,
  frontmatter_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(project_id, normalized_title)
);
```

The full migration should include indexes for `project_id`, `page_type`,
`normalized_title`, and `created_at`.

### 4.2 New package

Add `src/hippocampus/wiki/`:

| Module | Responsibility |
|--------|----------------|
| `paths.py` | Resolve wiki root, raw root, page directories, schema path. |
| `models.py` | Dataclasses for page metadata, source records, lint issues, ingest plans. |
| `frontmatter.py` | Read/write markdown frontmatter safely. |
| `naming.py` | Normalize titles and derive stable filenames. |
| `projects.py` | Resolve, create, and require initialized project wiki state. |
| `storage.py` | CRUD for `wiki_*` tables. |
| `workspace.py` | Initialize project state and optional materialized layout. |
| `index.py` | Render database-backed index pages. |
| `log.py` | Append and render database-backed log entries. |
| `export.py` | Materialize DB pages to markdown files. |
| `ingest.py` | Plan and apply source ingestion to DB records. |
| `query.py` | Locate relevant DB pages and assemble query context. |
| `lint.py` | Health checks for wiki structure and references. |

### 4.3 CLI surface

Add a `wiki` command group in `src/hippocampus/cli/main.py`:

```bash
hippo wiki init [--project <key>] [--export-root <path>] [--materialize]
hippo wiki status [--project <key>]
hippo wiki ingest <raw-path> [--project <key>] [--dry-run] [--materialize]
hippo wiki query "<question>" [--project <key>] [--file-title <title>]
hippo wiki file-answer <title> --project <key> --stdin [--materialize]
hippo wiki lint [--project <key>] [--json]
hippo wiki export [--project <key>] [--force]
hippo wiki index [--project <key>] [--render]
hippo wiki log [--project <key>] [--tail 20]
```

The first version may implement `query` as context assembly rather than a full
LLM answer generator, because the local CLI should not depend on an external LLM
API. The AI client can use the context and write the final answer.

Every command except `init` and `status` must call a shared
`require_project()` helper. If the project is missing, return a blocked response
and do no work.

### 4.4 MCP surface

Expose tools in `src/hippocampus/mcp/server.py`, backed by functions in
`mcp/tools.py`:

- `wiki_init`
- `wiki_status`
- `wiki_ingest`
- `wiki_query`
- `wiki_file_answer`
- `wiki_lint`

MCP outputs should be structured and agent-friendly. For example, `wiki_ingest`
should return:

```json
{
  "status": "planned|applied",
  "project": "hippocampus",
  "source_page": "wiki/sources/...",
  "pages_created": [],
  "pages_updated": [],
  "index_updated": true,
  "log_entry": "## [2026-06-15] ingest | ...",
  "warnings": []
}
```

If project state is missing, all MCP wiki tools must return:

```json
{
  "ok": false,
  "blocked": true,
  "reason": "wiki_not_initialized",
  "next_step": "Run wiki_init for this project before continuing."
}
```

### 4.5 Schema file

`schema/LLM-WIKI.md` should tell agents:

1. Raw sources are immutable.
2. Database wiki pages are canonical.
3. Do not inspect project markdown files before checking `wiki_status`.
4. If `wiki_status` says the project is missing, ask the user to initialize it.
5. Read the database-backed index first.
6. Update the database-backed log after every ingest/query-file/lint repair.
7. Preserve source references.
8. Prefer small, traceable edits over broad rewrites.
9. Ask the user before resolving contradictions that require judgment.
10. File useful query outputs into `analysis` pages when the user asks.

### 4.6 Relationship to fragments

The wiki layer should create Hippocampus fragments only for:

- Durable operating rules learned while maintaining the wiki.
- High-value synthesized conclusions the user wants available across all
  projects and clients.
- Session summaries produced by existing `end_progress` / auto-distill flows.

Do not duplicate every source summary as a fragment by default. That would
inflate long-term memory with content better represented as wiki pages.

## 5. Ingest Flow

Default supervised single-source flow:

1. User asks an AI client to ingest a source for a project.
2. Agent calls `wiki_status(project)` or `wiki_ingest(..., dry_run=true)`.
3. If missing, agent stops and asks the user to initialize the wiki first.
4. User runs `hippo wiki init --project <key>` or approves `wiki_init`.
5. Agent calls `wiki_ingest(raw_path, dry_run=true)` or CLI equivalent to get a
   structured plan.
6. Agent reads the source and existing relevant DB wiki pages.
7. Agent applies edits through wiki storage:
   - Create/update source page record.
   - Update related entity/concept/topic page records.
   - Add contradiction or open-question markers when needed.
   - Update rendered index content/records.
   - Append `wiki_log`.
8. Agent optionally materializes markdown files.
9. Agent optionally calls `remember()` only for durable cross-session lessons.
10. Agent reports created/updated pages and unresolved questions.

In V10, source understanding still happens in the AI agent. Hippocampus provides
the structure, database store, commands, validation, and context helpers.

## 6. Query Flow

1. Agent calls `wiki_status(project)`.
2. If missing, agent asks the user to initialize first.
3. Agent calls `wiki_query(question)` to read the database-backed index and
   locate candidate pages.
4. Agent reads the relevant page records and source page records.
5. Agent answers with citations.
6. If the result is worth preserving, user asks to file it.
7. Agent calls `wiki_file_answer`, which creates an `analysis` page record,
   updates index records, appends `wiki_log`, and optionally exports markdown.

## 7. Lint Flow

`hippo wiki lint` should produce structured issues:

| Code | Meaning |
|------|---------|
| `wiki_not_initialized` | Project has no initialized wiki record. |
| `missing_frontmatter` | Page lacks required YAML frontmatter. |
| `missing_index_entry` | Page exists but is absent from `index.md`. |
| `missing_log_entry` | Source page has no ingest entry in `log.md`. |
| `broken_wikilink` | Page links to a missing target. |
| `orphan_page` | Non-source page has no inbound links. |
| `duplicate_title` | Multiple pages normalize to the same title key. |
| `uncited_page` | Page type requires sources but has `sources: []`. |
| `contradiction_marker` | Page contains unresolved contradiction marker. |
| `stale_candidate` | Page may need review after newer related sources. |
| `materialization_drift` | Exported markdown differs from the DB record. |

Lint should not rewrite pages automatically in V10. It may return suggested
repairs for the agent to apply. The one exception is `wiki_not_initialized`:
the only repair is `hippo wiki init`, which must be explicit.

## 8. Data and Compatibility

- Add migration `006_wiki.sql`; no migration changes are required for existing
  fragment tables.
- Add wiki settings to `config._DEFAULTS`.
- Existing installs continue working when the wiki is disabled.
- `hippo doctor` may report wiki status only after initialization or when
  `wiki_enabled=true`.

## 9. Testing Strategy

Use temporary `HIPPOCAMPUS_HOME` and `HIPPOCAMPUS_VAULT` like existing tests.

Required test groups:

- Unit tests for project resolution, missing-project blocking, paths, naming,
  frontmatter parsing, index rendering, log appending, and lint issue detection.
- Storage tests for `wiki_*` CRUD and unique constraints.
- Integration tests for `hippo wiki init`.
- Integration tests proving `hippo wiki ingest/query/lint` block before init.
- Integration tests for markdown source ingest fixtures.
- Integration tests for query context assembly.
- MCP tests for JSON payload shape.
- Regression test proving existing fragment mirror behavior is unchanged.

## 10. Rollout

Ship in waves:

1. DB migration and project initialization gate.
2. Page/source/index/log storage primitives.
3. Optional markdown materialization/export.
4. CLI status/init/lint/index.
5. Ingest plan/apply for markdown and text.
6. Query and file-answer flows.
7. MCP tools.
8. Injection/docs/doctor updates.

The feature should remain behind settings until waves 1-5 are stable.

## 11. Open Implementation Decisions

- Whether MCP `wiki_ingest` should update page records directly or return an
  edit plan.
  Recommendation: direct DB writes for source/index/log records,
  agent-mediated page body edits for synthesis pages in V10.
- Whether to use title-case filenames or slug filenames.
  Recommendation: title-case filenames for Obsidian readability, with normalized
  duplicate detection.
- Whether source ids should be ULIDs or title-date slugs.
  Recommendation: title-date slug plus collision suffix for human readability.
- Whether materialization should happen after every write or only when requested.
  Recommendation: configurable, default explicit export to avoid file churn.
