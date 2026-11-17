-- Migration 016: rejected_facts. A human-rejected automatic fact is remembered
-- (by normalised-content hash, per project) so automatic producers do not
-- re-create it. Local-only: not part of device sync.

CREATE TABLE rejected_facts (
    project     TEXT NOT NULL DEFAULT '',   -- '' = global
    fact_hash   TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    source_type TEXT,
    rejected_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (project, fact_hash)
);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (16);
