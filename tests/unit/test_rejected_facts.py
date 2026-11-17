"""Rejected automatic facts are not re-created (migration 016)."""

from __future__ import annotations

import json
import shutil

from click.testing import CliRunner


def _auto(tools, text, project="proj", source_type="auto-remembered"):
    return tools.remember(content=text, summary="s", source_type=source_type, project=project)


def _reject(fid):
    from hippocampus.cli.main import cli
    return CliRunner().invoke(cli, ["review", "reject", fid])


def test_reject_blocks_same_fact_with_whitespace_case_variation(hippo_env):
    from hippocampus.mcp import tools

    fid = _auto(tools, "Never use tabs")["fragment"]["id"]
    assert json.loads(_reject(fid).output)["rejected"] == [fid]
    for variant in ("Never use tabs", "  never   USE\ttabs\n"):
        assert _auto(tools, variant) == {"stored": False, "reason": "rejected_before"}
        assert _auto(tools, variant, source_type="session-summary")["stored"] is False
    assert _auto(tools, "Never use tabs", project="other")["stored"] is True


def test_manual_remember_succeeds_and_clears_tombstone(hippo_env):
    from hippocampus.mcp import tools
    from hippocampus.storage import fragments as F

    _reject(_auto(tools, "Use spaces")["fragment"]["id"])
    assert len(F.list_rejected()) == 1
    assert tools.remember(content="use spaces", project="proj")["stored"] is True
    assert F.list_rejected() == []
    assert _auto(tools, "Use spaces")["stored"] is True


def test_decay_archive_writes_no_tombstone(hippo_env):
    from hippocampus.mcp import tools
    from hippocampus.storage import fragments as F

    fid = _auto(tools, "Old fact")["fragment"]["id"]
    F.archive(fid)
    assert F.list_rejected() == []
    assert _auto(tools, "Old fact")["stored"] is True


def test_cli_rejected_lists_and_restores(hippo_env):
    from hippocampus.cli.main import cli
    from hippocampus.mcp import tools

    _reject(_auto(tools, "Bad rule")["fragment"]["id"])
    runner = CliRunner()
    rows = json.loads(runner.invoke(cli, ["review", "rejected"]).output)
    assert [r["project"] for r in rows] == ["proj"]
    out = runner.invoke(cli, ["review", "rejected", "--restore", rows[0]["fact_hash"][:8]])
    assert json.loads(out.output) == {"restored": 1}
    assert _auto(tools, "Bad rule")["stored"] is True


def test_migration_016_on_existing_db(hippo_env, tmp_path, monkeypatch):
    from hippocampus.storage import db

    database = tmp_path / "v15.db"
    legacy = tmp_path / "migrations-15"
    legacy.mkdir()
    for m in db.MIGRATIONS_DIR.glob("*.sql"):
        if int(m.name.split("_", 1)[0]) <= 15:
            shutil.copy2(m, legacy / m.name)
    real = db.MIGRATIONS_DIR
    monkeypatch.setattr(db, "MIGRATIONS_DIR", legacy)
    db.init_db(database)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", real)
    with db.get_conn(database) as conn:
        conn.execute("INSERT INTO fragments(id, content) VALUES ('frag_a', 'x')")

    db.init_db(database)
    with db.get_conn(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM rejected_facts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM fragments").fetchone()[0] == 1
        assert conn.execute("SELECT 1 FROM schema_migrations WHERE version = 16").fetchone()


def test_fresh_db_has_rejected_facts(hippo_env):
    from hippocampus import config
    from hippocampus.storage import db

    with db.get_conn(config.DB_PATH) as conn:
        assert conn.execute("SELECT 1 FROM schema_migrations WHERE version = 16").fetchone()
        conn.execute("SELECT * FROM rejected_facts")


def test_cli_remember_dream_on_rejected_fact_reports_not_stored(hippo_env):
    from hippocampus.cli.main import cli
    from hippocampus.mcp import tools

    _reject(_auto(tools, "Dream fact", source_type="dream")["fragment"]["id"])
    res = CliRunner().invoke(
        cli, ["remember", "--source-type", "dream", "--project", "proj", "-c", "dream  FACT"]
    )
    assert res.exit_code == 0 and res.exception is None
    assert json.loads(res.output) == {"stored": False, "reason": "rejected_before"}


def test_quarantine_approve_of_rejected_fact_fails_cleanly(hippo_env):
    from hippocampus.a2a import store
    from hippocampus.cli.main import cli
    from hippocampus.mcp import tools

    _reject(_auto(tools, "Peer fact", source_type="dream")["fragment"]["id"])
    args = {"content": "peer fact", "source_type": "dream", "project": "proj"}
    qid = store.quarantine_add(store.db_path(), session_key="k", client="c", turn={}, args=args)
    res = CliRunner().invoke(cli, ["a2a", "quarantine", "approve", qid])
    assert res.exit_code == 1 and "rejected_before" in res.output
    assert store.quarantine_get(store.db_path(), qid) is not None
