"""Migration 015 over a DB that already holds 014-era data."""

from __future__ import annotations

import shutil


def test_migration_015_adds_columns_without_touching_values(hippo_env, tmp_path, monkeypatch):
    from hippocampus.storage import db

    database = tmp_path / "v14.db"
    legacy = tmp_path / "migrations-14"
    legacy.mkdir()
    for m in db.MIGRATIONS_DIR.glob("*.sql"):
        if int(m.name.split("_", 1)[0]) <= 14:
            shutil.copy2(m, legacy / m.name)
    real = db.MIGRATIONS_DIR
    monkeypatch.setattr(db, "MIGRATIONS_DIR", legacy)
    db.init_db(database)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", real)

    with db.get_conn(database) as conn:
        conn.execute(
            "INSERT INTO fragments(id, content, confidence, accessed, pinned) VALUES ('frag_a', 'x', 0.83, 7, 1)"
        )
        conn.execute("INSERT INTO sessions(id, client) VALUES ('sess_a', 'pytest')")
        conn.execute("INSERT INTO feedback_log(fragment_id, kind) VALUES ('frag_a', 'pin')")

    db.init_db(database)

    with db.get_conn(database) as conn:
        row = conn.execute("SELECT * FROM fragments WHERE id = 'frag_a'").fetchone()
        assert (row["confidence"], row["accessed"], row["pinned"]) == (0.83, 7, 1)
        assert (row["helped_count"], row["hurt_count"], row["evidence_at"]) == (0, 0, None)
        assert conn.execute("SELECT outcomes_settled_at FROM sessions").fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM feedback_log").fetchone()[0] == 1
        conn.execute(
            "INSERT INTO fragment_outcomes(session_id, fragment_id, outcome, strength, source) "
            "VALUES ('sess_a', 'frag_a', 'helped', 'weak', 'settle')"
        )
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 15
        conn.execute("DELETE FROM fragments WHERE id = 'frag_a'")
        assert conn.execute("SELECT COUNT(*) FROM fragment_outcomes").fetchone()[0] == 0
