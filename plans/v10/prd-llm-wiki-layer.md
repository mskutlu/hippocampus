# PRD V10 — LLM Wiki layer

> Status: drafted
> Author: @msk
> Target version: `1.7.0`

## 1. Problem

Hippocampus currently gives AI assistants a shared memory substrate:
synthesized fragments, semantic recall, working-session ledgers, transcript
history, automatic injection, and an Obsidian-readable fragment mirror. This is
useful for remembering durable lessons and current work, but it is not yet a
full implementation of the "LLM Wiki" pattern.

The missing layer is a persistent, structured markdown wiki that the LLM
maintains from curated source documents. In the LLM Wiki pattern, new sources
are not merely stored or indexed. They are compiled into interlinked topic,
entity, source, and synthesis pages. The wiki accumulates over time: cross-links
are maintained, contradictions are flagged, source coverage is visible, and good
answers can be filed back into the knowledge base.

Today Hippocampus can remember a source summary as a fragment, but it does not
own project-scoped wiki state, maintain named wiki pages, update `index.md` and
`log.md`, lint wiki health, or provide source-backed citations from generated
wiki pages. Users who want the LLM Wiki workflow must ask the agent to improvise
it manually each time.

The user also wants the wiki pages to be callable directly from Hippocampus
rather than forcing agents to inspect project directories first. Therefore V10
should treat the database as the canonical wiki store. Markdown files still
exist, but as materialized/exported views for Obsidian, git, and human review.

## 2. Goals

| ID | Goal |
|----|------|
| G1 | Add optional project-scoped LLM Wiki state stored canonically in SQLite. |
| G2 | Provide a source ingest workflow that turns one raw source into a source summary page and updates related entity, concept, topic, and synthesis pages in the database. |
| G3 | Keep the existing fragment/ledger system intact and use it as support memory, not as the canonical wiki. |
| G4 | Add schema instructions that teach Codex, Claude Code, Devin, Cursor, and other clients how to operate the wiki consistently. |
| G5 | Add CLI and MCP surfaces for wiki discovery, ingest planning, logging, linting, and querying. |
| G6 | Preserve source provenance. Every wiki claim that comes from a source must point back to a source page or source id. |
| G7 | Materialize markdown files for Obsidian or git only after a project wiki is initialized. |
| G8 | Add health checks for stale pages, orphan pages, missing backlinks, uncited claims, duplicate page names, and projects missing required wiki state. |
| G9 | Allow high-value query answers to be filed back into the wiki as durable analysis pages. |
| G10 | Make the feature opt-in and safe for existing Hippocampus installations. |
| G11 | If a project does not have initialized wiki state, stop the operation and ask the user to run/create the wiki first. |

## 3. User Stories

- **As a researcher**, I want to register or add an article, paper, transcript,
  or note and ask an AI client to ingest it so the relevant wiki pages become
  current without hand-editing them.
- **As a long-running project owner**, I want topic pages to evolve as more
  sources arrive, so I do not re-synthesize the same background on every query.
- **As a reader**, I want source pages and citations so I can audit where a
  claim came from before I trust it.
- **As a wiki maintainer**, I want `index.md` and `log.md` kept current so the
  AI can navigate the wiki without needing vector infrastructure at small scale.
- **As an Obsidian user**, I want Hippocampus to materialize normal markdown
  files with wikilinks, backlinks, tags, and frontmatter after the project wiki
  exists.
- **As a Hippocampus user**, I want important decisions and wiki operating rules
  remembered across AI clients without mixing them into the wiki content itself.
- **As an agent user**, I want the AI to tell me "this project wiki is not
  initialized yet" before it tries to ingest, query, or lint missing wiki files.

## 4. Functional Requirements

### 4.1 Database-backed project wiki

1. **FR-1** The system must add SQLite tables for project-scoped wiki state.
2. **FR-2** A wiki project must be uniquely identified by a stable project key
   derived from an explicit `--project` value or the current workspace path.
3. **FR-3** The database must store wiki pages as markdown bodies plus
   structured metadata.
4. **FR-4** The database must store raw-source records with source references,
   content hashes, source type, and optional local file paths.
5. **FR-5** The database must store wiki log entries chronologically instead of
   relying only on `wiki/log.md`.
6. **FR-6** The database must store or render an index view equivalent to
   `wiki/index.md`.
7. **FR-7** The system must require explicit project wiki initialization before
   ingest, query, file-answer, or lint operations continue.
8. **FR-8** If a project wiki is missing, CLI/MCP operations must return a
   clear blocked response that tells the user to run `hippo wiki init`.
9. **FR-9** Existing fragment tables and `~/hippocampus-vault/Fragments/` files
   must remain unchanged and separate from the wiki layer.

### 4.2 Materialized markdown workspace

10. **FR-10** The system must support a configurable wiki export root,
    defaulting to `~/hippocampus-vault/Wiki/`.
11. **FR-11** `hippo wiki init` must create or register a project wiki in the
    database and may create a materialized markdown tree:
    - `raw/` for immutable source files or pointers.
    - `wiki/` for generated markdown pages.
    - `wiki/sources/`
    - `wiki/entities/`
    - `wiki/concepts/`
    - `wiki/topics/`
    - `wiki/analyses/`
    - `wiki/index.md`
    - `wiki/log.md`
    - `schema/LLM-WIKI.md`
12. **FR-12** Markdown files must be reproducible from the database via export
    or sync commands.
13. **FR-13** When database and materialized markdown disagree, the database is
    canonical unless the user explicitly imports from disk.

### 4.3 Page conventions

14. **FR-14** All generated wiki pages must be stored as markdown-compatible
   content and exportable as markdown files with YAML frontmatter.
15. **FR-15** Frontmatter must include at least `title`, `type`, `created`,
   `updated`, `sources`, `tags`, and `status`.
16. **FR-16** Page types must include `source`, `entity`, `concept`, `topic`,
   `analysis`, `overview`, and `index`.
17. **FR-17** Internal links must use Obsidian-compatible wikilinks where
   practical, e.g. `[[concepts/Memory Consolidation]]`.
18. **FR-18** Claims copied or synthesized from sources must include source
   references using a stable source id or source page link.
19. **FR-19** The system must define stable filename normalization rules to
    avoid duplicate pages for the same entity or concept.

### 4.4 Source ingest

20. **FR-20** The system must ingest one source at a time by default.
21. **FR-21** Ingest must create or update a source page record in the database.
22. **FR-22** Ingest must identify candidate entities, concepts, topics,
    contradictions, and open questions from the source.
23. **FR-23** Ingest must update existing relevant pages when the source adds,
    corrects, strengthens, or contradicts prior knowledge.
24. **FR-24** Ingest must create missing entity/concept/topic pages only when
    there is enough source-backed content to justify a page.
25. **FR-25** Ingest must update the database-backed index.
26. **FR-26** Ingest must append one database log entry renderable as:
    `## [YYYY-MM-DD] ingest | <source title>`.
27. **FR-27** Ingest must optionally export changed pages to markdown after
    database writes complete.
28. **FR-28** Ingest must optionally create Hippocampus fragments for durable
    cross-session lessons, but the wiki pages remain the canonical wiki output.
29. **FR-29** Batch ingest may be supported later, but the first version must
    optimize for supervised single-source ingest.

### 4.5 Query and file-back

30. **FR-30** The system must provide a query workflow that reads the
    database-backed index first, searches relevant page records, reads
    source-backed content, and produces context for an answer with citations.
31. **FR-31** The system must allow the user to file a useful answer back as an
    `analysis` page record with frontmatter and source references.
32. **FR-32** Filed analyses must update the database-backed index and append a
    `query-filed` log entry.
33. **FR-33** Filed analyses may also create a Hippocampus fragment when they
    contain durable operating knowledge or a reusable conclusion.

### 4.6 Lint and maintenance

34. **FR-34** The system must provide a wiki lint command or MCP tool that
    reports, at minimum:
    - Project wiki not initialized.
    - Page records missing required metadata.
    - Page records missing index entries.
    - Source pages missing log entries.
    - Orphan non-source pages.
    - Broken wikilinks.
    - Duplicate normalized titles.
    - Pages with `sources: []` outside allowed page types.
    - Materialized markdown drift, when export files exist.
35. **FR-35** The lint workflow must flag likely stale claims when a page has
    older sources but related newer source pages exist.
36. **FR-36** The lint workflow must flag contradiction markers so the user can
    resolve or investigate them.
37. **FR-37** The system must expose a repair plan, but automatic repairs must
    require explicit user approval unless the repair is purely mechanical.

### 4.7 CLI and MCP

38. **FR-38** Add CLI commands under `hippo wiki`, including:
    - `hippo wiki init`
    - `hippo wiki status`
    - `hippo wiki ingest <raw-path>`
    - `hippo wiki query "<question>"`
    - `hippo wiki file-answer <title>`
    - `hippo wiki lint`
    - `hippo wiki export`
    - `hippo wiki index`
    - `hippo wiki log`
39. **FR-39** Add MCP tools for AI clients to perform the same workflows:
    `wiki_init`, `wiki_ingest`, `wiki_query`, `wiki_file_answer`,
    `wiki_lint`, and `wiki_status`.
40. **FR-40** MCP tools must return structured JSON payloads suitable for an AI
    agent to explain and act on.
41. **FR-41** CLI commands must be safe to run repeatedly.
42. **FR-42** Commands that require project wiki state must fail closed with a
    "wiki initialization required" response instead of creating ad hoc files.

### 4.8 Configuration and injection

43. **FR-43** Add settings for wiki enabled state, default export root, max
    files per ingest, index rendering limits, lint strictness, and
    materialization policy.
44. **FR-44** Update the injected Hippocampus protocol block to mention wiki
    workflows only when the wiki feature is enabled.
45. **FR-45** Add a generated wiki schema/instructions page/file that clients can
    read before editing wiki pages.
46. **FR-46** The schema must define page types, frontmatter, link conventions,
    ingest flow, query flow, lint flow, and human-review boundaries.

## 5. Non-Goals

- Replace the existing fragment store, recall, working ledger, transcript
  history, or Obsidian fragment mirror.
- Build a full document parser for every file type in V10. Markdown and plain
  text are required; PDFs and HTML can be handled through future adapters.
- Build a visual Obsidian plugin.
- Build collaborative permissions, review workflows, or multi-user approval
  gates in the first version.
- Guarantee fully automated contradiction resolution. The first version flags
  contradictions and records them; humans decide what to trust.
- Add external vector databases. The existing local semantic recall may be used
  later, but the wiki should work from markdown, index, and simple search first.

## 6. Design Considerations

- The exported wiki should feel like a normal Obsidian vault: readable
  filenames, wikilinks, frontmatter, and a graph-friendly structure.
- The AI owns the generated wiki pages, but raw sources are immutable and should
  not be modified by the AI.
- The database-backed index should render to a compact `index.md` that an AI can
  read before deciding which pages to inspect.
- The database-backed log should render to a parseable `log.md`, with entries
  such as
  `grep "^## \\[" wiki/log.md | tail -5`.
- The wiki should tolerate manual user inspection. Hand edits are allowed, but
  agents should treat existing user-authored text carefully and avoid wholesale
  rewrites unless the user asks.

## 7. Technical Considerations

- Add a new package area such as `src/hippocampus/wiki/` rather than mixing wiki
  logic into fragment storage.
- Use structured markdown/frontmatter parsing instead of ad hoc string slicing
  where possible.
- Keep the canonical source of wiki truth in SQLite for direct MCP/CLI access.
  Markdown files are exported/materialized views.
- Add a migration such as `006_wiki.sql` for `wiki_projects`, `wiki_pages`,
  `wiki_sources`, `wiki_page_sources`, `wiki_links`, and `wiki_log`.
- Reuse existing config, CLI, MCP server, and client-injection patterns.
- Use existing embeddings only as optional support. Wiki navigation should work
  without semantic extras installed.
- Add tests with temporary wiki roots, mirroring the existing test isolation
  pattern for `HIPPOCAMPUS_HOME` and `HIPPOCAMPUS_VAULT`.

## 8. Success Metrics

- **M-1** `hippo wiki init` creates a project wiki record and optional
  materialized layout idempotently.
- **M-2** Ingesting a markdown source creates database records for a source
  page, updates at least one topic/concept/entity page when relevant, updates
  the index, and appends a log row.
- **M-3** `hippo wiki lint` reports zero issues on a freshly initialized empty
  wiki and useful issues on intentionally broken fixtures.
- **M-4** A query can produce context from database-backed wiki pages with
  source references.
- **M-5** A filed answer appears in database-backed `analysis` pages, the index,
  and the log, and can be exported to markdown.
- **M-6** Existing Hippocampus tests continue to pass, proving the feature is
  additive.
- **M-7** Calling `wiki_ingest`, `wiki_query`, or `wiki_lint` for an
  uninitialized project returns a clear "initialize first" response.

## 9. Open Questions

- **Q-1** Should markdown materialization be automatic after every DB update, or
  explicit via `hippo wiki export`?
- **Q-2** Which source formats should V10 officially support beyond markdown and
  plain text?
- **Q-3** Should ingest create Hippocampus fragments automatically by default,
  or only when the agent/user explicitly marks a conclusion as durable memory?
- **Q-4** Should the MCP ingest tool actually update page records, or should it return a
  proposed edit plan for the agent to apply using normal file tools?
- **Q-5** Should generated page filenames preserve title case for Obsidian
  readability or use lowercase slugs for filesystem stability?
