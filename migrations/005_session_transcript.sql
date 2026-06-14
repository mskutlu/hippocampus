-- Migration 005: raw/visible session transcript history.
--
-- This stores conversation artifacts separately from synthesized fragments.
-- Fragments remain distilled long-term memory; transcript rows preserve raw
-- prompts, visible assistant responses, and explicit reasoning summaries.

CREATE TABLE IF NOT EXISTS session_transcript (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    client        TEXT NOT NULL,
    session_key   TEXT NOT NULL DEFAULT 'default',
    role          TEXT NOT NULL CHECK (
                    role IN (
                        'user',
                        'assistant',
                        'assistant_summary',
                        'reasoning_summary',
                        'system',
                        'tool'
                    )
                  ),
    content       TEXT NOT NULL,
    source_event  TEXT,
    metadata_json TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_session_transcript_session
    ON session_transcript(session_id, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_session_transcript_client
    ON session_transcript(client, session_key, created_at DESC);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (5);
