-- Migration 004: scoped session identity.
--
-- Multiple terminals can use the same AI client at the same time. A session is
-- therefore keyed by client + derived context (explicit override, TTY, cwd,
-- terminal-session hints), not by client alone.

ALTER TABLE sessions ADD COLUMN session_key TEXT NOT NULL DEFAULT 'default';

CREATE INDEX IF NOT EXISTS idx_sessions_client_key
    ON sessions(client, session_key, started_at DESC);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (4);
