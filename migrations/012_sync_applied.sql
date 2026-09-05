CREATE TABLE IF NOT EXISTS sync_applied (
    key  TEXT PRIMARY KEY,
    sig  TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_migrations(version) VALUES (12);
