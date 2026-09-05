from __future__ import annotations

import sqlite3
import shutil
from pathlib import Path


def test_backup_restores_consistent_database(hippo_env, tmp_path):
    from hippocampus.storage import backup, fragments

    fragment = fragments.create("backup truth")
    created = backup.create(retention=2)
    restored_path = tmp_path / "restored.db"

    restored = backup.restore(
        Path(created["path"]),
        destination=restored_path,
        safety_backup=False,
    )

    assert created["integrity"] == "ok"
    assert restored["integrity"] == "ok"
    with sqlite3.connect(restored_path) as conn:
        row = conn.execute("SELECT content FROM fragments WHERE id = ?", (fragment.id,)).fetchone()
    assert row == ("backup truth",)


def test_backup_retention_removes_oldest(hippo_env):
    from hippocampus.storage import backup, fragments

    fragments.create("retention")
    first = backup.create(retention=2)
    backup.create(retention=2)
    third = backup.create(retention=2)

    assert not Path(first["path"]).exists()
    assert Path(third["path"]).exists()


def test_failed_migration_restores_original_database(hippo_env, tmp_path, monkeypatch):
    import pytest

    from hippocampus import config
    from hippocampus.storage import db, fragments

    fragment = fragments.create("survives migration failure")
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "999_broken.sql").write_text(
        "CREATE TABLE partial_change(id INTEGER); INVALID SQL;",
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "MIGRATIONS_DIR", migration_dir)

    with pytest.raises(sqlite3.OperationalError):
        db.init_db(config.DB_PATH)

    assert fragments.get(fragment.id) is not None
    with sqlite3.connect(config.DB_PATH) as conn:
        partial = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='partial_change'"
        ).fetchone()
    assert partial is None


def test_integrity_migrations_upgrade_existing_database(hippo_env, tmp_path, monkeypatch):
    from hippocampus.storage import db

    all_migrations = db.MIGRATIONS_DIR
    legacy_migrations = tmp_path / "legacy-migrations"
    legacy_migrations.mkdir()
    for migration in all_migrations.glob("*.sql"):
        if int(migration.name.split("_", 1)[0]) <= 6:
            shutil.copy2(migration, legacy_migrations / migration.name)

    database = tmp_path / "legacy.db"
    monkeypatch.setattr(db, "MIGRATIONS_DIR", legacy_migrations)
    db.init_db(database)
    with db.get_conn(database) as conn:
        conn.execute(
            """
            INSERT INTO sessions(id, client, session_key, started_at)
            VALUES ('old', 'codex', 'same', '2020-01-01T00:00:00.000Z')
            """
        )
        conn.execute(
            """
            INSERT INTO sessions(id, client, session_key, started_at)
            VALUES ('new', 'codex', 'same', '2021-01-01T00:00:00.000Z')
            """
        )
        conn.execute(
            """
            INSERT INTO session_ledger(session_id, client, turn_index, kind, content)
            VALUES ('new', 'codex', 1, 'goal', 'keep this session')
            """
        )
        conn.execute(
            "INSERT INTO fragments(id, content, summary) VALUES ('frag_test', 'body', 'summary')"
        )
        conn.execute(
            "INSERT INTO feedback_log(fragment_id, kind) VALUES ('frag_test', 'archive')"
        )

    monkeypatch.setattr(db, "MIGRATIONS_DIR", all_migrations)
    db.init_db(database)

    with db.get_conn(database) as conn:
        open_sessions = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL"
        ).fetchone()[0]
        total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(feedback_log)")}
        foreign_keys = conn.execute("PRAGMA foreign_key_list(feedback_log)").fetchall()
        versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        conn.execute("DELETE FROM fragments WHERE id = 'frag_test'")
        audit_rows = conn.execute(
            "SELECT COUNT(*) FROM feedback_log WHERE fragment_id = 'frag_test'"
        ).fetchone()[0]

    assert open_sessions == 1
    assert total_sessions == 1
    assert "session_id" in columns
    assert foreign_keys == []
    assert versions == set(range(1, 10))
    assert audit_rows == 1
