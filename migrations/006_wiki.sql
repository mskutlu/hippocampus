-- Migration 006: database-backed LLM Wiki layer.
--
-- Wiki state is canonical in SQLite. Markdown files are optional exported
-- materialized views for Obsidian/git/human review.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS wiki_projects (
    id              TEXT PRIMARY KEY,
    project_key     TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    workspace_path  TEXT,
    export_root     TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_wiki_projects_key ON wiki_projects(project_key);
CREATE INDEX IF NOT EXISTS idx_wiki_projects_workspace ON wiki_projects(workspace_path);

CREATE TABLE IF NOT EXISTS wiki_sources (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES wiki_projects(id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    source_type    TEXT NOT NULL DEFAULT 'markdown',
    source_ref     TEXT,
    content_hash   TEXT,
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(project_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_wiki_sources_project ON wiki_sources(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wiki_sources_hash ON wiki_sources(project_id, content_hash);

CREATE TABLE IF NOT EXISTS wiki_pages (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES wiki_projects(id) ON DELETE CASCADE,
    page_type         TEXT NOT NULL CHECK (
                          page_type IN ('source', 'entity', 'concept', 'topic', 'analysis', 'overview', 'index', 'log', 'schema')
                      ),
    title             TEXT NOT NULL,
    normalized_title  TEXT NOT NULL,
    path              TEXT NOT NULL,
    markdown          TEXT NOT NULL,
    frontmatter_json  TEXT NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL DEFAULT 'draft',
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(project_id, normalized_title),
    UNIQUE(project_id, path)
);

CREATE INDEX IF NOT EXISTS idx_wiki_pages_project_type ON wiki_pages(project_id, page_type);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_project_updated ON wiki_pages(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS wiki_page_sources (
    page_id    TEXT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
    source_id  TEXT NOT NULL REFERENCES wiki_sources(id) ON DELETE CASCADE,
    PRIMARY KEY (page_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_wiki_page_sources_source ON wiki_page_sources(source_id);

CREATE TABLE IF NOT EXISTS wiki_links (
    page_id      TEXT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
    target_path  TEXT NOT NULL,
    target_title TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (page_id, target_path)
);

CREATE INDEX IF NOT EXISTS idx_wiki_links_target ON wiki_links(target_path);

CREATE TABLE IF NOT EXISTS wiki_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     TEXT NOT NULL REFERENCES wiki_projects(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,
    title          TEXT NOT NULL,
    details        TEXT,
    page_id        TEXT REFERENCES wiki_pages(id) ON DELETE SET NULL,
    source_id      TEXT REFERENCES wiki_sources(id) ON DELETE SET NULL,
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_wiki_log_project_created ON wiki_log(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wiki_log_kind ON wiki_log(project_id, kind);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (6);
