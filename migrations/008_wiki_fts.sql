CREATE VIRTUAL TABLE IF NOT EXISTS wiki_pages_fts USING fts5(
    title,
    markdown,
    path,
    content='wiki_pages',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS wiki_pages_ai AFTER INSERT ON wiki_pages BEGIN
    INSERT INTO wiki_pages_fts(rowid, title, markdown, path)
    VALUES (new.rowid, new.title, new.markdown, new.path);
END;

CREATE TRIGGER IF NOT EXISTS wiki_pages_ad AFTER DELETE ON wiki_pages BEGIN
    INSERT INTO wiki_pages_fts(wiki_pages_fts, rowid, title, markdown, path)
    VALUES ('delete', old.rowid, old.title, old.markdown, old.path);
END;

CREATE TRIGGER IF NOT EXISTS wiki_pages_au AFTER UPDATE ON wiki_pages BEGIN
    INSERT INTO wiki_pages_fts(wiki_pages_fts, rowid, title, markdown, path)
    VALUES ('delete', old.rowid, old.title, old.markdown, old.path);
    INSERT INTO wiki_pages_fts(rowid, title, markdown, path)
    VALUES (new.rowid, new.title, new.markdown, new.path);
END;

INSERT INTO wiki_pages_fts(wiki_pages_fts) VALUES ('rebuild');

INSERT OR IGNORE INTO schema_migrations(version) VALUES (8);
