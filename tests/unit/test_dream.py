"""Dreaming: gated dream facts, `review list --json` neighbors, CLI plumbing."""

from __future__ import annotations

import json

import pytest

from tests.integration.test_semantic_recall import StubProvider

TEXT = "kafka consumers must be idempotent on every retry " * 20


@pytest.fixture
def semantic_env(hippo_env):
    from hippocampus import embeddings

    embeddings.reset_provider()
    embeddings.set_provider(StubProvider())
    yield hippo_env
    embeddings.reset_provider()


def _run(args):
    from click.testing import CliRunner
    from hippocampus.cli.main import cli

    return CliRunner().invoke(cli, args)


def _review_json(*args):
    out = _run(["review", "list", "--json", *args])
    assert out.exit_code == 0, out.output
    return json.loads(out.output)


def test_dream_facts_are_candidates(hippo_env):
    from hippocampus.mcp import tools

    out = tools.remember("one durable fact", source_type="dream")

    assert out["fragment"]["review_state"] == "candidate"


def test_review_list_json_filters_source_types_and_nulls_neighbors(hippo_env):
    from hippocampus.storage import fragments as F

    summary = F.create("blob", source_type="session-summary", project="alpha", source_ref="sess-1")
    rule = F.create("rule", source_type="auto-remembered")
    dream = F.create("fact", source_type="dream")
    F.create("manual", source_type="manual")

    both = _review_json("--source-type", "session-summary", "--source-type", "auto-remembered")
    assert {i["id"] for i in both} == {summary.id, rule.id}
    item = next(i for i in both if i["id"] == summary.id)
    assert item["project"] == "alpha" and item["source_ref"] == "sess-1" and item["content"] == "blob"
    assert item["neighbors"] is None
    assert {i["id"] for i in _review_json("--source-type", "dream")} == {dream.id}
    assert [f.id for f in F.list_candidates(source_type="dream")] == [dream.id]
    assert "neighbors" not in _review_json("--source-type", "dream", "--neighbors", "0")[0]


def test_review_list_json_neighbors_are_approved_or_dream_same_project(semantic_env):
    from hippocampus.mcp import tools

    def store(**kw):
        return tools.remember(TEXT, **kw)["fragment"]["id"]

    cand = store(project="alpha", source_type="session-summary")
    approved = store(project="alpha")
    dream = store(project="alpha", source_type="dream")
    old = store(project="alpha")
    new = tools.remember(TEXT, project="alpha", supersedes=old)["fragment"]["id"]
    store(project="alpha", source_type="session-summary")
    store(project="beta")
    store(scope="global")

    item = next(i for i in _review_json("--source-type", "session-summary") if i["id"] == cand)

    assert {n["id"] for n in item["neighbors"]} == {approved, dream, new}
    assert {n["id"]: n["review_state"] for n in item["neighbors"]} == {
        approved: "approved", dream: "candidate", new: "approved",
    }
    assert {n["source_type"] for n in item["neighbors"]} == {"manual", "dream"}
    assert all(n["score"] >= 0.7 for n in item["neighbors"])
    assert all(len(n["content"]) == 400 for n in item["neighbors"])
    capped = _review_json("--source-type", "session-summary", "--neighbors", "1")
    assert all(len(i["neighbors"]) <= 1 for i in capped)
    strict = _review_json("--source-type", "session-summary", "--threshold", "1.5")
    assert all(i["neighbors"] == [] for i in strict)


def test_review_list_json_neighbors_not_starved_by_raw_candidates(semantic_env):
    from hippocampus.mcp import tools

    for _ in range(5):
        tools.remember(TEXT, project="alpha", source_type="session-summary")
    approved = tools.remember(TEXT, project="alpha")["fragment"]["id"]

    items = _review_json("--source-type", "session-summary", "--neighbors", "1")

    assert len(items) == 5
    assert all([n["id"] for n in i["neighbors"]] == [approved] for i in items)


def test_cli_remember_project_and_global(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPOCAMPUS_PROJECT", "alpha")

    def stored(*flags):
        out = _run(["remember", "-c", "a fact", *flags])
        assert out.exit_code == 0, out.output
        return json.loads(out.output)["fragment"]["project"]

    assert stored() == "alpha"
    assert stored("--project", "beta") == "beta"
    assert stored("--global") is None
    both = _run(["remember", "-c", "a fact", "--project", "beta", "--global"])
    assert both.exit_code != 0 and "mutually exclusive" in both.output


def test_cli_dream_prints_prompt(hippo_env):
    out = _run(["dream", "--limit", "7"])

    assert out.exit_code == 0, out.output
    assert "--limit 7" in out.output and "{limit}" not in out.output
    assert "hippo remember --source-type dream" in out.output
    assert "hippo review reject" in out.output


def test_review_reject_skips_non_candidates(hippo_env):
    from hippocampus.storage import fragments as F

    candidate = F.create("raw blob", source_type="session-summary")
    approved = F.create("approved fact")

    out = _run(["review", "reject", candidate.id, approved.id, "frag_missing"])

    assert out.exit_code == 0, out.output
    assert json.loads(out.output) == {
        "rejected": [candidate.id],
        "skipped": [{"id": approved.id, "reason": "not a candidate"}],
        "missing": ["frag_missing"],
    }
    assert F.get(candidate.id) is None
    assert F.get(approved.id) is not None


def test_review_list_hides_superseded_candidates(hippo_env):
    from hippocampus.mcp import tools

    old = tools.remember("old fact", source_type="dream", project="alpha")["fragment"]["id"]
    new = tools.remember("new fact", source_type="dream", project="alpha", supersedes=old)["fragment"]["id"]

    assert {i["id"] for i in _review_json("--source-type", "dream")} == {new}
