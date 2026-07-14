PRAGMA foreign_keys = OFF;

CREATE TABLE feedback_log_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fragment_id  TEXT NOT NULL,
    session_id   TEXT,
    kind         TEXT NOT NULL,
    delta        REAL,
    reason       TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO feedback_log_new (id, fragment_id, kind, delta, reason, created_at)
SELECT id, fragment_id, kind, delta, reason, created_at
FROM feedback_log;

DROP TABLE feedback_log;
ALTER TABLE feedback_log_new RENAME TO feedback_log;

CREATE INDEX idx_feedback_fragment
    ON feedback_log(fragment_id, created_at DESC);
CREATE INDEX idx_feedback_session
    ON feedback_log(session_id, created_at DESC);

UPDATE sessions
SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY client, session_key
                   ORDER BY started_at DESC, id DESC
               ) AS row_number
        FROM sessions
        WHERE ended_at IS NULL
    )
    WHERE row_number > 1
);

DELETE FROM sessions
WHERE NOT EXISTS (
    SELECT 1 FROM session_ledger WHERE session_id = sessions.id
)
AND NOT EXISTS (
    SELECT 1 FROM session_accesses WHERE session_id = sessions.id
)
AND NOT EXISTS (
    SELECT 1 FROM session_transcript WHERE session_id = sessions.id
);

CREATE UNIQUE INDEX idx_sessions_one_open
    ON sessions(client, session_key)
    WHERE ended_at IS NULL;

INSERT OR IGNORE INTO schema_migrations(version) VALUES (7);

PRAGMA foreign_keys = ON;
