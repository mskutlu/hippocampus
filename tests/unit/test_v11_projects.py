"""V11 Phase 2 — project scoping: rules, resolution, scoped recall/inject, backfill."""

from __future__ import annotations

import json
import subprocess

import pytest


RULES = {
    "acme": {
        "remotes": ["gitlab.com/acme/*"],
        "paths": ["~/work/acme-*", "~/work/acme-legacy-*"],
        "aliases": ["acme-orders", "customer-a", "acme-*"],
    },
    "personal": {"remotes": ["github.com/me/*"], "paths": [], "aliases": []},
    "hippo": {"remotes": ["github.com/me/hippocampus"], "paths": ["~/work/hippocampus"], "aliases": []},
}


def _write_rules(hippo_env, rules=RULES):
    from hippocampus import projects

    projects.save(rules)
    return projects


def test_normalize_remote(hippo_env):
    from hippocampus import projects

    assert projects.normalize_remote("https://user@gitlab.com/acme/acme-orders.git") == "gitlab.com/acme/acme-orders"
    assert projects.normalize_remote("git@github.com:me/hippocampus.git") == "github.com/me/hippocampus"
    assert projects.normalize_remote("ssh://git@bitbucket.globex.example:7999/bss/qa-suite.git") == "bitbucket.globex.example/7999/bss/qa-suite"


def test_remote_most_specific_wins(hippo_env):
    projects = _write_rules(hippo_env)
    assert projects.match_remote("github.com/me/hippocampus") == "hippo"
    assert projects.match_remote("github.com/me/other") == "personal"
    assert projects.match_remote("gitlab.com/acme/acme-orders") == "acme"
    assert projects.match_remote("example.com/x") is None


def test_path_matches_ancestors(hippo_env, monkeypatch, tmp_path):
    projects = _write_rules(hippo_env)
    monkeypatch.setenv("HOME", str(tmp_path))
    work = tmp_path / "work" / "acme-orders" / "src" / "main"
    work.mkdir(parents=True)
    assert projects.match_path(work) == "acme"
    assert projects.match_path(tmp_path / "work" / "other") is None


def test_resolve_order_env_marker_remote_path(hippo_env, monkeypatch, tmp_path):
    projects = _write_rules(hippo_env)
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "work" / "acme-planning"
    repo.mkdir(parents=True)

    # path rule
    assert projects.resolve(repo) == "acme"

    # remote beats path
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/me/other.git"], check=True)
    projects._resolve_cache.clear(); projects._remote_cache.clear()
    assert projects.resolve(repo) == "personal"

    # marker beats remote
    (repo / ".hippocampus-project").write_text("hippo\n")
    projects._resolve_cache.clear()
    assert projects.resolve(repo) == "hippo"

    # env beats everything
    monkeypatch.setenv("HIPPOCAMPUS_PROJECT", "acme")
    assert projects.resolve(repo) == "acme"

    # nothing matches -> global
    monkeypatch.delenv("HIPPOCAMPUS_PROJECT")
    assert projects.resolve(tmp_path) is None


def test_session_records_project_and_fragments_inherit(hippo_env, monkeypatch, tmp_path):
    projects = _write_rules(hippo_env)
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "work" / "acme-legacy-sales"
    repo.mkdir(parents=True)
    monkeypatch.setenv("HIPPOCAMPUS_CWD", str(repo))

    from hippocampus.mcp import tools as T
    from hippocampus.storage import sessions

    sid = sessions.open_session("pytest")
    assert sessions.session_project(sid) == "acme"
    frag = T.remember(content="sales orders need a currency")["fragment"]
    assert frag["project"] == "acme"
    glob = T.remember(content="always answer in English", scope="global")["fragment"]
    assert glob["project"] is None


def test_recall_hides_other_projects_and_scope_all_reveals(hippo_env, monkeypatch, tmp_path):
    projects = _write_rules(hippo_env)
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    F.create("kafka consumers in acme are idempotent", summary="kafka acme", project="acme")
    F.create("kafka consumers in globex use messaging", summary="kafka globex", project="globex")
    F.create("kafka global rule: never autocommit", summary="kafka global")

    monkeypatch.setenv("HIPPOCAMPUS_PROJECT", "acme")
    from hippocampus.storage import sessions
    sessions.open_session("pytest")

    out = T.recall(query="kafka")
    got = {f["project"] for f in out["fragments"]}
    assert got == {"acme", None}
    assert out["project"] == "acme" and out["scope"] == "project"

    everything = T.recall(query="kafka", scope="all")
    assert {f["project"] for f in everything["fragments"]} == {"acme", "globex", None}

    only_global = T.recall(query="kafka", scope="global")
    assert {f["project"] for f in only_global["fragments"]} == {None}


def test_semantic_candidates_are_filtered_before_ranking(hippo_env, monkeypatch):
    from hippocampus.embeddings import search as semantic_search
    from hippocampus.storage import fragments as F

    a = F.create("a", summary="a", project="acme")
    b = F.create("b", summary="b", project="globex")
    allowed = F.ids_in_scope("acme", "project")
    assert a.id in allowed and b.id not in allowed
    assert F.ids_in_scope(None, "all") is None


def test_top_n_scopes(hippo_env):
    from hippocampus.dynamics import ranking
    from hippocampus.storage import fragments as F

    F.create("l", summary="l", project="acme")
    F.create("e", summary="e", project="globex")
    F.create("g", summary="g")
    assert {f.project for f in ranking.top_n(scope="global")} == {None}
    assert {f.project for f in ranking.top_n(project="acme", scope="project")} == {"acme", None}
    assert {f.project for f in ranking.top_n(scope="all")} == {"acme", "globex", None}


def test_backfill_from_session_key_and_tags(hippo_env):
    projects = _write_rules(hippo_env)
    from hippocampus.storage import fragments as F
    from hippocampus.storage.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions(id, client, session_key, started_at) VALUES "
            "('sess_a', 'codex', 'tty-ttys003-cwd-acme-orders-5bbe5d2dc2f97439', '2026-01-01T00:00:00.000Z')"
        )
    by_key = F.create("x", summary="x", source_type="session-summary", source_ref="sess_a")
    by_tag = F.create("y", summary="y", tags=["acme-qa", "devin"])
    by_name = F.create("z", summary="z", tags=["hippo"])
    none = F.create("w", summary="w", tags=["devin"])

    preview = projects.backfill(dry_run=True)
    assert preview["assigned"] == {"acme": 2, "hippo": 1} and preview["unmatched"] == 1
    assert F.get(by_key.id).project is None

    projects.backfill(dry_run=False)
    assert F.get(by_key.id).project == "acme"
    assert F.get(by_tag.id).project == "acme"
    assert F.get(by_name.id).project == "hippo"
    assert F.get(none.id).project is None
    assert F.project_counts() == {"acme": 2, "hippo": 1, "(global)": 1}


def test_hook_payload_names_project(hippo_env, monkeypatch):
    _write_rules(hippo_env)
    monkeypatch.setenv("HIPPOCAMPUS_PROJECT", "acme")
    from hippocampus.clients import hook_context
    from hippocampus.storage import fragments as F, sessions

    sessions.open_session("pytest")
    F.create("kafka in acme", summary="kafka acme", project="acme")
    F.create("kafka in globex", summary="kafka globex", project="globex")
    payload = hook_context.render_context(client="pytest", query="kafka", extra_query_streams=[])
    assert "project `acme` + global" in payload
    assert "kafka acme" in payload and "kafka globex" not in payload
