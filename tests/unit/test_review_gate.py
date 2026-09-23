"""Review gate for auto-generated fragments + supersede links (migration 014)."""

from __future__ import annotations

import json
import shutil

import pytest


def _migrate_to(version, tmp_path, monkeypatch, database):
    from hippocampus.storage import db

    legacy = tmp_path / f"migrations-{version}"
    legacy.mkdir()
    for migration in db.MIGRATIONS_DIR.glob("*.sql"):
        if int(migration.name.split("_", 1)[0]) <= version:
            shutil.copy2(migration, legacy / migration.name)
    all_migrations = db.MIGRATIONS_DIR
    monkeypatch.setattr(db, "MIGRATIONS_DIR", legacy)
    db.init_db(database)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", all_migrations)


def test_migration_014_backfills_review_state_and_unpins_auto_pins(hippo_env, tmp_path, monkeypatch):
    from hippocampus.storage import db

    database = tmp_path / "v13.db"
    _migrate_to(13, tmp_path, monkeypatch, database)
    seed = [
        ("frag_autopinned", "session-summary", 1),
        ("frag_repinned", "session-summary", 1),
        ("frag_pinned_no_log", "session-summary", 1),
        ("frag_rule", "auto-remembered", 0),
        ("frag_rule_pinned", "auto-remembered", 1),
        ("frag_manual", "manual", 1),
    ]
    feedback = [
        ("frag_autopinned", "pin"),
        ("frag_autopinned", "unpin"),
        ("frag_autopinned", "auto-pin"),
        ("frag_repinned", "auto-pin"),
        ("frag_repinned", "unpin"),
        ("frag_repinned", "pin"),
        ("frag_rule_pinned", "pin"),
        ("frag_manual", "auto-pin"),
    ]
    with db.get_conn(database) as conn:
        conn.executemany(
            "INSERT INTO fragments(id, content, source_type, pinned) VALUES (?, 'body', ?, ?)", seed
        )
        conn.executemany("INSERT INTO feedback_log(fragment_id, kind) VALUES (?, ?)", feedback)

    db.init_db(database)

    with db.get_conn(database) as conn:
        rows = {
            r["id"]: (r["review_state"], r["pinned"], r["superseded_by"])
            for r in conn.execute("SELECT id, review_state, pinned, superseded_by FROM fragments")
        }
        unpins = conn.execute(
            "SELECT fragment_id FROM feedback_log WHERE kind = 'unpin' AND reason = 'review-gate-migration'"
        ).fetchall()
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]

    assert rows == {
        "frag_autopinned": ("candidate", 0, None),
        "frag_repinned": ("approved", 1, None),
        "frag_pinned_no_log": ("candidate", 0, None),
        "frag_rule": ("candidate", 0, None),
        "frag_rule_pinned": ("approved", 1, None),
        "frag_manual": ("approved", 1, None),
    }
    assert sorted(r["fragment_id"] for r in unpins) == ["frag_autopinned", "frag_pinned_no_log"]
    assert version == 14


def test_create_derives_review_state(hippo_env):
    from hippocampus.storage import fragments as F

    assert F.create("s", source_type="session-summary").review_state == "candidate"
    assert F.create("r", source_type="auto-remembered").review_state == "candidate"
    assert F.create("p", source_type="session-summary", pinned=True).review_state == "approved"
    assert F.create("m").review_state == "approved"


def test_distilled_session_is_a_candidate(hippo_env):
    from hippocampus.mcp import tools as T

    T.log_progress(kind="goal", content="ship the review gate")
    out = T.end_progress(distill_to_fragment=True, summary="review gate shipped")
    assert out["distilled_fragment"]["review_state"] == "candidate"


def test_auto_pin_skips_candidates_and_superseded(hippo_env, monkeypatch):
    from hippocampus.dynamics import boost as boost_dyn
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    monkeypatch.setenv("HIPPO_AUTO_PIN_ACCESS_THRESHOLD", "2")
    candidate = F.create("candidate body", source_type="session-summary")
    old = T.remember(content="old fact", scope="global")["fragment"]["id"]
    T.remember(content="new fact", scope="global", supersedes=old)
    approved = F.create("approved body")

    for _ in range(3):
        for fid in (candidate.id, old, approved.id):
            boost_dyn.boost(fid, client="pytest")

    assert F.get(candidate.id).pinned is False
    assert F.get(old).pinned is False
    assert F.get(approved.id).pinned is True


def test_top_n_excludes_candidates_and_superseded(hippo_env):
    from hippocampus.dynamics import ranking
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    candidate = F.create("candidate", source_type="auto-remembered")
    pinned_candidate = F.create("pinned summary", source_type="session-summary")
    F.update_fields(pinned_candidate.id, pinned=True)
    old = T.remember(content="old", scope="global")["fragment"]["id"]
    new = T.remember(content="new", scope="global", supersedes=old)["fragment"]["id"]
    F.update_fields(old, pinned=True)
    approved = F.create("approved")

    ids = {f.id for f in ranking.top_n(limit=50)}
    assert ids == {new, approved.id}
    assert candidate.id in {f.id for f in F.list_all()}


def test_hook_fragment_section_excludes_candidates(hippo_env):
    from hippocampus.clients import hook_context
    from hippocampus.storage import fragments as F

    candidate = F.create("zebracorn deployment notes", summary="zebracorn candidate", source_type="session-summary")
    approved = F.create("zebracorn deployment runbook", summary="zebracorn approved")

    lines = "\n".join(hook_context._fragment_section("zebracorn deployment", limit=5, extra_query_streams=[]))
    assert approved.id in lines
    assert candidate.id not in lines

    fallback = "\n".join(hook_context._fragment_section(None, limit=5))
    assert approved.id in fallback
    assert candidate.id not in fallback


def test_recall_returns_candidates_but_not_superseded(hippo_env):
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    candidate = F.create("quokka cache eviction", source_type="session-summary")
    old = T.remember(content="quokka cache ttl is 5 minutes", scope="global")["fragment"]["id"]
    new = T.remember(content="quokka cache ttl is 10 minutes", scope="global", supersedes=old)["fragment"]["id"]

    out = T.recall("quokka cache", limit=5, scope="all")
    states = {f["id"]: f["review_state"] for f in out["fragments"]}
    assert states == {candidate.id: "candidate", new: "approved"}

    gated = T.recall("quokka cache", limit=5, scope="all", include_candidates=False)
    assert [f["id"] for f in gated["fragments"]] == [new]


def test_pin_approves_candidate(hippo_env):
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    candidate = F.create("summary", source_type="session-summary")
    out = T.pin(candidate.id)
    assert out["fragment"]["pinned"] is True
    assert F.get(candidate.id).review_state == "approved"


def test_review_state_change_bumps_updated_at(hippo_env):
    from hippocampus.storage import fragments as F

    candidate = F.create("summary", source_type="session-summary")
    updated = F.update_fields(candidate.id, review_state="approved")
    assert updated.updated_at > candidate.updated_at


def test_remember_supersedes_links_old_fragment(hippo_env):
    from hippocampus.mcp import tools as T
    from hippocampus.storage.db import get_ro_conn

    old = T.remember(content="postgres port is 5433", project="alpha")["fragment"]["id"]
    out = T.remember(content="postgres port is 5432", project="alpha", supersedes=old)
    new = out["fragment"]["id"]

    assert out["superseded"]["id"] == old
    assert out["superseded"]["superseded_by"] == new
    fetched = T.get_fragment(old, boost_on_read=False)
    assert fetched["found"] is True
    assert fetched["fragment"]["superseded_by"] == new
    with get_ro_conn() as conn:
        row = conn.execute(
            "SELECT reason FROM feedback_log WHERE fragment_id = ? AND kind = 'supersede'", (old,)
        ).fetchone()
    assert row["reason"] == new


@pytest.mark.parametrize("case", ["missing", "already", "project"])
def test_remember_supersedes_rejects_invalid_targets(hippo_env, case):
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    old = T.remember(content="old", project="alpha")["fragment"]["id"]
    if case == "already":
        T.remember(content="newer", project="alpha", supersedes=old)
    target = "frag_does_not_exist" if case == "missing" else old
    project = "beta" if case == "project" else "alpha"
    before = F.count()

    with pytest.raises(ValueError):
        T.remember(content="replacement", project=project, supersedes=target)
    assert F.count() == before


def test_supersede_links_existing_fragments_and_guards(hippo_env):
    from hippocampus.mcp import tools as T

    old = T.remember(content="old", project="alpha")["fragment"]["id"]
    new = T.remember(content="new", project="alpha")["fragment"]["id"]
    other = T.remember(content="other", project="beta")["fragment"]["id"]

    with pytest.raises(ValueError):
        T.supersede(old, other)
    with pytest.raises(ValueError):
        T.supersede(old, old)
    assert T.supersede(old, new)["superseded"]["superseded_by"] == new
    with pytest.raises(ValueError):
        T.supersede(new, old)


def _run(args):
    from click.testing import CliRunner
    from hippocampus.cli.main import cli

    return CliRunner().invoke(cli, args)


def test_cli_review_list_approve_reject(hippo_env):
    from hippocampus.storage import fragments as F

    summary = F.create("s body", summary="summary one", source_type="session-summary")
    rule = F.create("r body", summary="rule one", source_type="auto-remembered")
    F.create("manual body", summary="manual one")

    listed = _run(["review", "list"])
    assert listed.exit_code == 0, listed.output
    assert listed.output.splitlines()[0].startswith(rule.id)
    assert summary.id in listed.output and "manual one" not in listed.output

    only_rules = _run(["review", "list", "--source-type", "auto-remembered"])
    assert rule.id in only_rules.output and summary.id not in only_rules.output

    approved = _run(["review", "approve", summary.id, "frag_missing"])
    assert approved.exit_code == 0, approved.output
    assert json.loads(approved.output) == {"approved": [summary.id], "missing": ["frag_missing"]}
    assert F.get(summary.id).review_state == "approved"
    assert F.get(summary.id).pinned is False

    rejected = _run(["review", "reject", rule.id])
    assert rejected.exit_code == 0, rejected.output
    assert json.loads(rejected.output)["rejected"] == [rule.id]
    assert F.get(rule.id) is None
    assert (hippo_env["fragments_dir"] / ".archive" / f"{rule.id}.md").exists()


def test_cli_supersede_and_remember_flag(hippo_env, monkeypatch):
    from hippocampus.storage import fragments as F

    monkeypatch.setenv("HIPPOCAMPUS_PROJECT", "alpha")
    old = F.create("old", project="alpha")
    new = F.create("new", project="alpha")

    linked = _run(["supersede", old.id, new.id])
    assert linked.exit_code == 0, linked.output
    assert F.get(old.id).superseded_by == new.id

    again = _run(["remember", "-c", "newest", "--supersedes", old.id])
    assert again.exit_code != 0
    assert "already superseded" in again.output

    ok = _run(["remember", "-c", "newest", "--supersedes", new.id])
    assert ok.exit_code == 0, ok.output
    assert F.get(new.id).superseded_by == json.loads(ok.output)["fragment"]["id"]


def test_audit_reports_candidates(hippo_env):
    from hippocampus import maintenance
    from hippocampus.storage import fragments as F

    F.create("s", source_type="session-summary")
    F.create("m")
    assert maintenance.audit()["metrics"]["candidate_fragments"] == 1


def test_sync_apply_ops_writes_review_fields(hippo_env):
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F
    from hippocampus.sync import client as sync_client

    base = {
        "content": "c", "summary": "s", "confidence": 0.5, "accessed": 0, "pinned": 0,
        "created_at": "2026-01-01T00:00:00.000Z", "updated_at": "2026-01-01T00:00:00.000Z", "tags": [],
    }
    ops = [
        {"entity": "fragment", "entity_id": "frag_new_peer", "op": "upsert", "payload": {
            **base, "id": "frag_new_peer", "source_type": "manual",
            "review_state": "approved", "superseded_by": "frag_x",
        }},
        {"entity": "fragment", "entity_id": "frag_old_peer", "op": "upsert", "payload": {
            **base, "id": "frag_old_peer", "source_type": "session-summary",
        }},
    ]
    sync_client.apply_ops(ops)

    assert F.get("frag_new_peer").superseded_by == "frag_x"
    assert F.get("frag_old_peer").review_state == "candidate"

    old = F.create("old", source_type="session-summary")
    new = F.create("new", source_type="session-summary")
    T.supersede(old.id, new.id)
    payloads = {op["entity_id"]: op["payload"] for op in sync_client.collect_ops("") if op["entity"] == "fragment"}
    assert payloads[old.id]["superseded_by"] == new.id
    assert payloads[new.id]["review_state"] == "candidate"
