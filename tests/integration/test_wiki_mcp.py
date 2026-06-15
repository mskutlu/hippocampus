from __future__ import annotations


def test_wiki_mcp_functions_block_and_initialize(hippo_env):
    from hippocampus.mcp import tools

    blocked = tools.wiki_query(question="anything", project="demo")
    assert blocked["blocked"] is True
    assert blocked["reason"] == "wiki_not_initialized"

    init = tools.wiki_init(project="demo")
    assert init["ok"] is True
    status = tools.wiki_status(project="demo")
    assert status["ok"] is True
    assert status["pages"] >= 3


def test_health_snapshot_reports_wiki_counts(hippo_env):
    from hippocampus import maintenance
    from hippocampus.mcp import tools

    tools.wiki_init(project="demo")
    health = maintenance.health_snapshot()
    assert health["wiki"]["projects"] == 1
    assert health["wiki"]["pages"] >= 3
