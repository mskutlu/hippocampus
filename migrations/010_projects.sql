ALTER TABLE fragments ADD COLUMN project TEXT;
ALTER TABLE sessions ADD COLUMN project TEXT;
CREATE INDEX IF NOT EXISTS idx_fragments_project ON fragments(project);
INSERT OR IGNORE INTO schema_migrations(version) VALUES (10);
