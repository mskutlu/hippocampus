CREATE INDEX IF NOT EXISTS idx_associations_b ON associations(fragment_b, weight DESC);
CREATE INDEX IF NOT EXISTS idx_associations_a ON associations(fragment_a, weight DESC);
INSERT OR IGNORE INTO schema_migrations(version) VALUES (13);
