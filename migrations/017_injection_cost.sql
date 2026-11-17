-- Migration 017: hook injection cost + "sent since last SessionStart" marker.
--
-- inject_epoch_at is stamped by every SessionStart/PostCompaction emission;
-- fragments accessed before it were lost with the compacted/cleared context.

ALTER TABLE sessions ADD COLUMN injected_chars INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN injected_emissions INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN inject_epoch_at TEXT;

INSERT OR IGNORE INTO schema_migrations(version) VALUES (17);
