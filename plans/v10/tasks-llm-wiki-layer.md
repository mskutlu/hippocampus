# V10 — LLM Wiki layer — task list

PRD: [prd-llm-wiki-layer.md](./prd-llm-wiki-layer.md)
Plan: [plan-llm-wiki-layer.md](./plan-llm-wiki-layer.md)
Roadmap: [roadmap-llm-wiki-layer.md](./roadmap-llm-wiki-layer.md)

## Relevant Files

- `migrations/006_wiki.sql` - Adds database-backed project wiki tables.
- `src/hippocampus/config.py` - Adds wiki settings and defaults.
- `src/hippocampus/wiki/__init__.py` - New wiki package.
- `src/hippocampus/wiki/models.py` - Dataclasses for projects, pages, sources, logs, lint issues, and operation results.
- `src/hippocampus/wiki/projects.py` - Project-key derivation, initialization checks, and missing-project blocking.
- `src/hippocampus/wiki/storage.py` - CRUD for `wiki_*` tables.
- `src/hippocampus/wiki/frontmatter.py` - Markdown frontmatter parsing and rendering.
- `src/hippocampus/wiki/naming.py` - Page title normalization and path derivation.
- `src/hippocampus/wiki/workspace.py` - Project initialization and default page creation.
- `src/hippocampus/wiki/index.py` - Database-backed index rendering.
- `src/hippocampus/wiki/log.py` - Database-backed log appending/rendering.
- `src/hippocampus/wiki/export.py` - Materializes database wiki pages to markdown files.
- `src/hippocampus/wiki/lint.py` - Wiki health checks.
- `src/hippocampus/wiki/ingest.py` - Source ingest planning and application.
- `src/hippocampus/wiki/query.py` - Query context assembly and answer filing helpers.
- `src/hippocampus/cli/main.py` - Adds the `hippo wiki` command group.
- `src/hippocampus/mcp/tools.py` - Adds callable wiki tool implementations.
- `src/hippocampus/mcp/server.py` - Exposes wiki MCP tools.
- `src/hippocampus/clients/injector.py` - Adds optional wiki workflow guidance to injection text.
- `src/hippocampus/clients/hook_context.py` - Optionally includes wiki status/context in hook payloads.
- `docs/ARCHITECTURE.md` - Documents DB-backed wiki state.
- `docs/RUNBOOK.md` - Documents wiki operations and troubleshooting.
- `README.md` - Adds LLM Wiki overview and usage.
- `CHANGELOG.md` - Release notes for V10.

## Test Files

- `tests/unit/test_wiki_projects.py` - Project-key derivation and missing-project gate.
- `tests/unit/test_wiki_storage.py` - Wiki DB CRUD and constraints.
- `tests/unit/test_wiki_frontmatter.py` - Frontmatter parse/render behavior.
- `tests/unit/test_wiki_naming.py` - Filename/title normalization.
- `tests/unit/test_wiki_index_log.py` - Index and log rendering.
- `tests/unit/test_wiki_export.py` - Markdown materialization.
- `tests/unit/test_wiki_lint.py` - Lint issue detection.
- `tests/integration/test_wiki_cli.py` - CLI init/status/export/lint behavior.
- `tests/integration/test_wiki_ingest.py` - Markdown/text source ingest.
- `tests/integration/test_wiki_query.py` - Query and file-answer flow.
- `tests/integration/test_wiki_mcp.py` - MCP JSON payloads and blocked responses.

## Notes

- Database state is canonical. Exported markdown files are materialized views.
- Do not let ingest/query/lint create ad hoc wiki files for an uninitialized
  project. The operation must stop and tell the user to initialize first.
- Keep existing fragment memory behavior unchanged.
- Follow existing test isolation with temporary `HIPPOCAMPUS_HOME` and
  `HIPPOCAMPUS_VAULT`.
- As implementation proceeds, check off completed tasks in this file.

## Tasks

- [x] 0.0 Create feature branch
  - [x] 0.1 Create and checkout a branch such as `feature/llm-wiki-layer`.
  - [x] 0.2 Confirm current worktree status and avoid touching unrelated changes.

- [x] 1.0 Add database schema and project initialization gate
  - [x] 1.1 Create `migrations/006_wiki.sql`.
  - [x] 1.2 Add `wiki_projects` table with project key, title, workspace path, export root, and timestamps.
  - [x] 1.3 Add `wiki_pages` table with markdown body, page type, normalized title, path, metadata JSON, and status.
  - [x] 1.4 Add `wiki_sources` table with title, source type, source ref/path, content hash, and timestamps.
  - [x] 1.5 Add `wiki_page_sources`, `wiki_links`, and `wiki_log` tables.
  - [x] 1.6 Add indexes and unique constraints for project/page lookup.
  - [x] 1.7 Add `wiki_enabled`, `wiki_default_export_root`, `wiki_materialize_default`, and related settings to `config._DEFAULTS`.
  - [x] 1.8 Implement `wiki.projects.derive_project_key()`.
  - [x] 1.9 Implement `wiki.projects.require_project()` that returns a structured `wiki_not_initialized` blocker.
  - [x] 1.10 Add unit tests for schema, project-key derivation, and missing-project blocking.

- [x] 2.0 Implement wiki storage primitives
  - [x] 2.1 Create `src/hippocampus/wiki/models.py`.
  - [x] 2.2 Create `src/hippocampus/wiki/storage.py`.
  - [x] 2.3 Implement project CRUD helpers.
  - [x] 2.4 Implement page create/update/get/list helpers.
  - [x] 2.5 Implement source create/update/get/list helpers.
  - [x] 2.6 Implement page-source citation relationship helpers.
  - [x] 2.7 Implement wikilink graph storage helpers.
  - [x] 2.8 Implement append-only wiki log helpers.
  - [x] 2.9 Add unit tests for storage success paths and constraint failures.

- [x] 3.0 Implement markdown conventions and rendering
  - [x] 3.1 Create `wiki/frontmatter.py` using structured YAML parsing/rendering.
  - [x] 3.2 Create `wiki/naming.py` for normalized titles and export paths.
  - [x] 3.3 Create `wiki/index.py` to render index markdown from DB pages.
  - [x] 3.4 Create `wiki/log.py` to render parseable log markdown from DB rows.
  - [x] 3.5 Define default schema text for `schema/LLM-WIKI.md`.
  - [x] 3.6 Add unit tests for frontmatter, naming, index, and log rendering.

- [x] 4.0 Implement project init and markdown materialization
  - [x] 4.1 Create `wiki/workspace.py`.
  - [x] 4.2 Implement idempotent project initialization.
  - [x] 4.3 Create default DB records for overview, index, log, and schema.
  - [x] 4.4 Create `wiki/export.py`.
  - [x] 4.5 Implement export of DB pages to deterministic markdown files.
  - [x] 4.6 Implement optional `raw/`, `wiki/`, and `schema/` directory creation.
  - [x] 4.7 Add drift detection helpers for exported markdown.
  - [x] 4.8 Add unit and integration tests for init and export.

- [x] 5.0 Add CLI wiki command group
  - [x] 5.1 Add `hippo wiki` command group in `cli/main.py`.
  - [x] 5.2 Add `hippo wiki init`.
  - [x] 5.3 Add `hippo wiki status`.
  - [x] 5.4 Add `hippo wiki export`.
  - [x] 5.5 Add `hippo wiki index --render`.
  - [x] 5.6 Add `hippo wiki log --tail`.
  - [x] 5.7 Ensure non-init operations call `require_project()`.
  - [x] 5.8 Add CLI integration tests, including blocked-before-init behavior.

- [x] 6.0 Implement wiki lint and health
  - [x] 6.1 Create `wiki/lint.py`.
  - [x] 6.2 Detect missing project initialization.
  - [x] 6.3 Detect missing metadata/frontmatter-equivalent fields.
  - [x] 6.4 Detect missing index and log coverage.
  - [x] 6.5 Detect broken wikilinks and orphan pages.
  - [x] 6.6 Detect duplicate normalized titles.
  - [x] 6.7 Detect uncited pages and contradiction markers.
  - [x] 6.8 Detect materialization drift when exported files exist.
  - [x] 6.9 Add `hippo wiki lint`.
  - [x] 6.10 Add tests for each lint issue code.

- [x] 7.0 Implement source ingest
  - [x] 7.1 Create `wiki/ingest.py`.
  - [x] 7.2 Implement raw markdown/plain-text source loading and hashing.
  - [x] 7.3 Implement duplicate-source detection.
  - [x] 7.4 Implement dry-run ingest plans.
  - [x] 7.5 Implement source page record creation/update.
  - [x] 7.6 Implement related page update hooks for agent-supplied entity/concept/topic edits.
  - [x] 7.7 Update DB-backed index after ingest.
  - [x] 7.8 Append `ingest` rows to `wiki_log`.
  - [x] 7.9 Add optional materialization after ingest.
  - [x] 7.10 Add `hippo wiki ingest`.
  - [x] 7.11 Add integration tests for dry-run, apply, duplicate source, and materialize modes.

- [x] 8.0 Implement query and file-answer
  - [x] 8.1 Create `wiki/query.py`.
  - [x] 8.2 Implement query candidate selection from index, titles, tags, links, and FTS where available.
  - [x] 8.3 Return structured context with page ids, titles, snippets, and source refs.
  - [x] 8.4 Add `hippo wiki query`.
  - [x] 8.5 Implement analysis page creation from stdin.
  - [x] 8.6 Update index and append `query-filed` log rows for filed answers.
  - [x] 8.7 Add optional materialization after file-answer.
  - [x] 8.8 Add `hippo wiki file-answer`.
  - [x] 8.9 Add integration tests for query and file-answer.

- [x] 9.0 Expose wiki MCP tools
  - [x] 9.1 Add wiki tool functions in `mcp/tools.py`.
  - [x] 9.2 Add `wiki_init`.
  - [x] 9.3 Add `wiki_status`.
  - [x] 9.4 Add `wiki_lint`.
  - [x] 9.5 Add `wiki_ingest`.
  - [x] 9.6 Add `wiki_query`.
  - [x] 9.7 Add `wiki_file_answer`.
  - [x] 9.8 Register tools in `mcp/server.py`.
  - [x] 9.9 Ensure missing-project responses are structured and consistent.
  - [x] 9.10 Add MCP integration tests.

- [x] 10.0 Update client guidance and context injection
  - [x] 10.1 Update `clients/injector.py` to include wiki workflow guidance only when enabled.
  - [x] 10.2 Update hook context to include compact wiki status within budget.
  - [x] 10.3 Ensure instructions say "check DB/project wiki status first".
  - [x] 10.4 Ensure instructions say "initialize/create markdown files before continuing" when missing.
  - [x] 10.5 Add tests for injected guidance toggles.

- [x] 11.0 Documentation and release
  - [x] 11.1 Update `README.md` with the LLM Wiki workflow.
  - [x] 11.2 Update `docs/ARCHITECTURE.md` with DB-backed wiki tables and flow.
  - [x] 11.3 Update `docs/RUNBOOK.md` with init, ingest, export, lint, query, and troubleshooting.
  - [x] 11.4 Update `CHANGELOG.md`.
  - [x] 11.5 No version bump required because this is unreleased work.

- [x] 12.0 Verification
  - [x] 12.1 Run unit tests for wiki modules.
  - [x] 12.2 Run integration tests for CLI and MCP.
  - [x] 12.3 Run full `pytest tests -q`.
  - [x] 12.4 Manual smoke: query before init blocks with `wiki_not_initialized`.
  - [x] 12.5 Manual smoke: init creates DB records and materialized markdown.
  - [x] 12.6 Manual smoke: ingest fixture source writes DB records and log row.
  - [x] 12.7 Manual smoke: export writes deterministic markdown.
  - [x] 12.8 Manual smoke: lint reports healthy initialized wiki.

## Done Definition

- The project wiki must be database-backed and project-scoped.
- Missing wiki state must block operations and ask for initialization first.
- Markdown files must be created/materialized from DB records, not used as the
  hidden canonical state.
- CLI and MCP tools must expose the same behavior.
- Existing Hippocampus memory features must remain compatible.
