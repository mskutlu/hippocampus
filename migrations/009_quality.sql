ALTER TABLE session_accesses ADD COLUMN via TEXT NOT NULL DEFAULT 'recall';

CREATE TABLE IF NOT EXISTS fragment_tombstones (
    fragment_id  TEXT PRIMARY KEY,
    deleted_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TRIGGER IF NOT EXISTS fragments_tombstone AFTER DELETE ON fragments BEGIN
    INSERT OR REPLACE INTO fragment_tombstones(fragment_id, deleted_at)
        VALUES (old.id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;

INSERT OR IGNORE INTO schema_migrations(version) VALUES (9);
