CREATE TABLE IF NOT EXISTS sync_state (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
INSERT OR IGNORE INTO schema_migrations(version) VALUES (11);
