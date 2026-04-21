-- Migration 001: initial schema for Hippocampus V1.
--
-- Tables:
--   fragments            synthesized atomic memories
--   fragment_tags        many-to-many tag assignments
--   associations         undirected co-access edges (canonical a < b ordering)
--   sessions             per-client sessions
--   session_accesses     which fragments were touched in which session
--   feedback_log         audit trail of negative/positive/pin/unpin events
--   schema_migrations    applied-migration bookkeeping
--
-- FTS5 virtual table keeps fragment content searchable; triggers keep it
-- synchronised with the main fragments table.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS fragments (
    id                       TEXT PRIMARY KEY,
    content                  TEXT NOT NULL,
    summary                  TEXT NOT NULL DEFAULT '',
    source_type              TEXT NOT NULL DEFAULT 'manual',
    source_ref               TEXT,
    confidence               REAL NOT NULL DEFAULT 0.5
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    accessed                 INTEGER NOT NULL DEFAULT 0 CHECK (accessed >= 0),
    last_accessed_at         TEXT,
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    pinned                   INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    below_threshold_since    TEXT  -- set when confidence first dips below ARCHIVE_THRESHOLD; cleared on recovery
);

CREATE INDEX IF NOT EXISTS idx_fragments_confidence    ON fragments(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_fragments_last_accessed ON fragments(last_accessed_at DESC);
CREATE INDEX IF NOT EXISTS idx_fragments_pinned        ON fragments(pinned);

CREATE TABLE IF NOT EXISTS fragment_tags (
    fragment_id  TEXT NOT NULL REFERENCES fragments(id) ON DELETE CASCADE,
    tag          TEXT NOT NULL,
    PRIMARY KEY (fragment_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_fragment_tags_tag ON fragment_tags(tag);

CREATE TABLE IF NOT EXISTS associations (
    fragment_a            TEXT NOT NULL REFERENCES fragments(id) ON DELETE CASCADE,
    fragment_b            TEXT NOT NULL REFERENCES fragments(id) ON DELETE CASCADE,
    weight                REAL NOT NULL DEFAULT 1.0 CHECK (weight >= 0.0),
    co_accessed_count     INTEGER NOT NULL DEFAULT 1 CHECK (co_accessed_count >= 0),
    last_co_accessed_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (fragment_a, fragment_b),
    CHECK (fragment_a < fragment_b)
);

CREATE INDEX IF NOT EXISTS idx_associations_weight ON associations(weight DESC);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    client      TEXT NOT NULL,
    started_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ended_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_client ON sessions(client, started_at DESC);

CREATE TABLE IF NOT EXISTS session_accesses (
    session_id   TEXT NOT NULL REFERENCES sessions(id)  ON DELETE CASCADE,
    fragment_id  TEXT NOT NULL REFERENCES fragments(id) ON DELETE CASCADE,
    accessed_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (session_id, fragment_id)
);

CREATE INDEX IF NOT EXISTS idx_session_accesses_fragment ON session_accesses(fragment_id);

CREATE TABLE IF NOT EXISTS feedback_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fragment_id  TEXT NOT NULL REFERENCES fragments(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,                       -- 'negative' | 'pin' | 'unpin' | 'decay' | 'boost' | 'archive'
    delta        REAL,
    reason       TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_feedback_fragment ON feedback_log(fragment_id, created_at DESC);

-- FTS5 mirror of fragments.{content, summary}. We use external-content mode
-- so we don't duplicate data; triggers keep it in sync.
CREATE VIRTUAL TABLE IF NOT EXISTS fragments_fts USING fts5(
    content,
    summary,
    content='fragments',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS fragments_ai AFTER INSERT ON fragments BEGIN
    INSERT INTO fragments_fts(rowid, content, summary)
        VALUES (new.rowid, new.content, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS fragments_ad AFTER DELETE ON fragments BEGIN
    INSERT INTO fragments_fts(fragments_fts, rowid, content, summary)
        VALUES ('delete', old.rowid, old.content, old.summary);
END;

CREATE TRIGGER IF NOT EXISTS fragments_au AFTER UPDATE ON fragments BEGIN
    INSERT INTO fragments_fts(fragments_fts, rowid, content, summary)
        VALUES ('delete', old.rowid, old.content, old.summary);
    INSERT INTO fragments_fts(rowid, content, summary)
        VALUES (new.rowid, new.content, new.summary);
END;

INSERT OR IGNORE INTO schema_migrations(version) VALUES (1);
