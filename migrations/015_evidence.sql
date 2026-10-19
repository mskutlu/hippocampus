-- Migration 015: evidence-driven confidence (opt-in via confidence_model).
--
-- Adds outcome counters, a per-session outcome ledger and a rebase backup.
-- Existing values are untouched; the default model stays 'access'.

ALTER TABLE fragments ADD COLUMN helped_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fragments ADD COLUMN hurt_count   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fragments ADD COLUMN evidence_at  TEXT;
ALTER TABLE sessions  ADD COLUMN outcomes_settled_at TEXT;

CREATE TABLE fragment_outcomes (
    session_id  TEXT NOT NULL,
    fragment_id TEXT NOT NULL REFERENCES fragments(id) ON DELETE CASCADE,
    outcome     TEXT NOT NULL CHECK (outcome IN ('helped', 'neutral', 'hurt')),
    strength    TEXT NOT NULL CHECK (strength IN ('weak', 'medium', 'strong')),
    source      TEXT NOT NULL,
    delta       REAL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (session_id, fragment_id, source)
);

CREATE INDEX idx_outcomes_fragment ON fragment_outcomes(fragment_id);

CREATE TABLE confidence_rebase_backup (
    fragment_id TEXT PRIMARY KEY,
    confidence  REAL,
    pinned      INTEGER,
    accessed    INTEGER,
    rebased_at  TEXT
);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (15);
