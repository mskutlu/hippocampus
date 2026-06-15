# V10 Roadmap — LLM Wiki layer

PRD: [prd-llm-wiki-layer.md](./prd-llm-wiki-layer.md)
Plan: [plan-llm-wiki-layer.md](./plan-llm-wiki-layer.md)

## Phase 0 — Initialization Gate and Storage Design

**Goal:** Make wiki operations impossible to run accidentally against a project
that has no wiki state.

Deliverables:

- Finalize `006_wiki.sql` schema.
- Add project-key derivation from explicit `--project` or workspace path.
- Add `wiki_projects` storage functions.
- Add shared `require_project()` behavior for CLI and MCP.
- Define the blocked response for missing wiki state.

Acceptance:

- `hippo wiki status` can report initialized/missing state.
- `hippo wiki ingest/query/lint/file-answer` all block before init.
- Missing-project MCP responses include `blocked=true`,
  `reason=wiki_not_initialized`, and an actionable next step.

## Phase 1 — Wiki Project Initialization

**Goal:** Create a project wiki record and default schema/index/log records
before any work starts.

Deliverables:

- `hippo wiki init --project <key>`.
- `wiki_init` MCP tool.
- Default `overview`, `index`, `log`, and schema page records.
- Optional materialized markdown tree under the configured export root.

Acceptance:

- Running init twice is idempotent.
- The initialized project can be queried by key and workspace path.
- Exported markdown includes `schema/LLM-WIKI.md`, `wiki/index.md`, and
  `wiki/log.md` when materialization is enabled.

## Phase 2 — Page, Source, Index, and Log Primitives

**Goal:** Build the low-level wiki storage layer before source ingest.

Deliverables:

- CRUD helpers for `wiki_pages`, `wiki_sources`, `wiki_page_sources`,
  `wiki_links`, and `wiki_log`.
- Frontmatter parsing/rendering.
- Filename/title normalization.
- Link extraction.
- Index rendering from DB rows.
- Log rendering from DB rows.

Acceptance:

- Unit tests cover create/update/delete page lifecycle.
- Duplicate normalized titles are rejected or flagged.
- Rendered `index.md` and `log.md` are stable and parseable.

## Phase 3 — Materialized Markdown Export

**Goal:** Support the user's "create md files then work" flow without making
the filesystem canonical.

Deliverables:

- `hippo wiki export --project <key>`.
- Optional `--materialize` on init, ingest, and file-answer.
- Drift detection between DB records and exported markdown.
- Export-safe path handling.

Acceptance:

- Export writes deterministic markdown files from DB records.
- Existing exported files are updated atomically.
- `wiki lint` can report materialization drift.
- No existing `Fragments/` mirror behavior changes.

## Phase 4 — Lint and Health

**Goal:** Give agents and users a reliable health check before wiki work.

Deliverables:

- `hippo wiki lint`.
- `wiki_lint` MCP tool.
- Lint issue codes for missing metadata, missing index entries, missing log
  entries, broken links, orphans, duplicate titles, uncited pages,
  contradiction markers, stale candidates, and materialization drift.
- `hippo doctor` integration when wiki is enabled or initialized.

Acceptance:

- Fresh initialized wiki has zero blocking lint errors.
- Broken fixture wiki produces expected issue codes.
- Missing wiki state is reported as an initialization requirement, not as random
  missing files.

## Phase 5 — Source Ingest

**Goal:** Turn one markdown/plain-text source into database-backed wiki pages.

Deliverables:

- `hippo wiki ingest <raw-path> --dry-run`.
- `hippo wiki ingest <raw-path>` apply mode.
- `wiki_ingest` MCP tool.
- Source record hashing and duplicate-source detection.
- Source page generation.
- Related page update hooks for entities, concepts, topics, and analyses.
- Index/log updates.

Acceptance:

- Ingesting a markdown fixture creates a source page record and log entry.
- Ingest can update existing pages without duplicate titles.
- Re-ingesting the same unchanged source is idempotent or returns a clear
  duplicate/no-op response.
- Dry-run returns a useful plan without writing.

## Phase 6 — Query and File-Answer

**Goal:** Let agents answer from DB wiki pages and preserve good answers.

Deliverables:

- `hippo wiki query "<question>"`.
- `wiki_query` MCP tool.
- Query context assembly from index, page titles, tags, links, and optional
  semantic recall.
- `hippo wiki file-answer <title> --stdin`.
- `wiki_file_answer` MCP tool.
- Analysis page creation, index update, log entry, and optional export.

Acceptance:

- Query returns relevant page records and source references.
- File-answer creates an analysis page and appends `query-filed` to the log.
- Filed answers can be exported to markdown.

## Phase 7 — Client Instructions and Injection

**Goal:** Teach AI clients the new database-first wiki workflow.

Deliverables:

- Update injected protocol text when wiki is enabled.
- Add schema instructions explaining:
  - Check `wiki_status` before wiki work.
  - Initialize before ingest/query/lint if missing.
  - DB is canonical.
  - Markdown files are materialized views.
  - Preserve sources and log entries.
- Update hook context to include wiki state only within budget.

Acceptance:

- AI clients receive clear "init first" behavior.
- Existing non-wiki users do not see noisy wiki instructions.
- Hook payload remains bounded.

## Phase 8 — Documentation and Release

**Goal:** Make the feature understandable and supportable.

Deliverables:

- README section for LLM Wiki.
- Architecture doc update for DB-backed wiki state.
- Runbook commands and troubleshooting.
- Changelog entry.
- Version bump if releasing.

Acceptance:

- Docs explain database canonical state and markdown export clearly.
- Docs include the "create/init first, then work" flow.
- Full test suite passes.

## Release Criteria

- `pytest tests -q` green.
- New wiki tests cover init gate, storage, export, lint, ingest, query, and MCP
  payloads.
- Existing fragment memory behavior remains unchanged.
- Manual smoke:
  1. `hippo wiki query "x"` before init blocks.
  2. `hippo wiki init --project hippocampus --materialize` creates DB state and
     markdown files.
  3. `hippo wiki ingest raw/inbox/example.md --dry-run` returns a plan.
  4. `hippo wiki ingest raw/inbox/example.md --materialize` writes DB records
     and exports markdown.
  5. `hippo wiki lint` returns expected health.
