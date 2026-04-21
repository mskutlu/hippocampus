-- Migration 003: vector embeddings for fragments.
--
-- Vectors are stored as raw little-endian float32 bytes. dim and model
-- columns let us coexist with embeddings from different models during a
-- migration (we can re-embed lazily).

CREATE TABLE IF NOT EXISTS fragment_embeddings (
    fragment_id  TEXT PRIMARY KEY REFERENCES fragments(id) ON DELETE CASCADE,
    vector       BLOB NOT NULL,
    dim          INTEGER NOT NULL,
    model        TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_fragment_embeddings_model
    ON fragment_embeddings(model);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (3);
