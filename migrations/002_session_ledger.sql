-- Migration 002: working-memory session ledger.
--
-- Each entry is a structured observation during a session: what the user
-- asked, what the AI did, what decisions were made, what's blocked.
--
-- The ledger is rendered into a marker-delimited WORKING block in each
-- client's rules file, so the AI sees a fresh, compaction-safe map of the
-- current task on every turn.

CREATE TABLE IF NOT EXISTS session_ledger (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    client       TEXT NOT NULL,
    turn_index   INTEGER NOT NULL,
    kind         TEXT NOT NULL CHECK (
                     kind IN ('goal', 'ask', 'done', 'blocker', 'decision', 'next', 'note')
                 ),
    content      TEXT NOT NULL,
    details      TEXT,
    resolved     INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_session_ledger_session ON session_ledger(session_id, turn_index DESC);
CREATE INDEX IF NOT EXISTS idx_session_ledger_client  ON session_ledger(client, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_ledger_kind    ON session_ledger(session_id, kind);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (2);
