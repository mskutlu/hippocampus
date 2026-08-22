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
