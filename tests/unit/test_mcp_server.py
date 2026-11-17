"""Unit tests for the MCP server's tool dispatcher and tool specs."""

from __future__ import annotations

import asyncio


def test_handle_call_tool_unknown_tool_raises(hippo_env):
    import pytest

    from hippocampus.mcp import server

    with pytest.raises(ValueError, match="unknown tool"):
        asyncio.run(server.handle_call_tool("nope", {}))


def test_handle_call_tool_missing_required_arg_raises(hippo_env):
    import pytest

    from hippocampus.mcp import server

    with pytest.raises(TypeError):
        asyncio.run(server.handle_call_tool("recall", {}))


def test_handle_call_tool_success_returns_plain_dict(hippo_env):
    from hippocampus.mcp import server

    result = asyncio.run(server.handle_call_tool("get_stats", {}))
    assert isinstance(result, dict)


def test_every_tool_spec_declares_annotations(hippo_env):
    from hippocampus.mcp import server

    for tool in server.TOOL_SPECS:
        assert tool.annotations is not None, f"{tool.name} missing annotations"
        assert tool.annotations.readOnlyHint is not None, f"{tool.name} missing readOnlyHint"


def _listed(server):
    return {t.name for t in asyncio.run(server.handle_list_tools())}


def test_list_tools_hides_unused_feature_tools(hippo_env, monkeypatch):
    from hippocampus import config
    from hippocampus.mcp import server
    from hippocampus.storage import db as sdb
    from hippocampus.wiki import storage

    sdb.init_db()
    wiki = {"wiki_status", "wiki_lint", "wiki_export", "wiki_ingest", "wiki_query",
            "wiki_file_answer", "wiki_index", "wiki_log"}
    a2a = {"send_message", "inbox", "reply", "list_agents"}

    def check(wiki_listed, a2a_on):
        monkeypatch.setenv("HIPPOCAMPUS_A2A_ENABLED", "1" if a2a_on else "0")
        names = _listed(server)
        assert "recall" in names and "wiki_init" in names
        assert (wiki <= names) is wiki_listed and bool(wiki & names) is wiki_listed
        assert (a2a <= names) is a2a_on and bool(a2a & names) is a2a_on

    for a2a_on in (False, True):
        check(False, a2a_on)  # flag off, no project
    storage.create_project("p", "P", None, None)
    for a2a_on in (False, True):
        check(True, a2a_on)  # flag off, project exists
    config.set_setting("wiki_enabled", True)
    for a2a_on in (False, True):
        check(True, a2a_on)  # flag on


def test_hidden_wiki_tool_still_callable(hippo_env):
    from hippocampus.mcp import server

    result = asyncio.run(server.handle_call_tool("wiki_status", {}))
    assert "error" not in result
