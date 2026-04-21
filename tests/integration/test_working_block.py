"""Integration test: log_progress -> working block file is refreshed immediately."""

from __future__ import annotations

from pathlib import Path


def test_log_progress_refreshes_client_block(hippo_env, tmp_path, monkeypatch):
    """Simulate: AI calls log_progress → WORKING block appears in the client's rules file."""
    from hippocampus.clients import registry
    from hippocampus.mcp import tools
    from hippocampus import config as hcfg

    # Redirect the devin client's rules path to a temp file so we don't touch real user state.
    fake_rules = tmp_path / "fake_agents.md"
    fake_rules.write_text("# Fake Rules\n", encoding="utf-8")
    orig_devin = registry.by_name("devin")
    fake_spec = type(orig_devin)(
        name="devin",
        label="Devin CLI",
        rules_path=fake_rules,
        creation_header="# Fake",
        mcp_config_path=None,
        mcp_config_format="devin-json",
    )
    # Replace devin in-place in the CLIENTS list.
    registry.CLIENTS[:] = [
        fake_spec if c.name == "devin" else c for c in registry.CLIENTS
    ]

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")

    out = tools.log_progress(kind="goal", content="Finish working memory")
    assert out["logged"] is True

    # Block must exist in the rules file now
    text = fake_rules.read_text()
    assert hcfg.WORKING_MARKER_START in text
    assert hcfg.WORKING_MARKER_END in text
    assert "Finish working memory" in text
    assert "# Fake Rules" in text  # pre-existing content preserved

    # Another entry → block grows
    tools.log_progress(kind="done", content="Implemented 002 migration")
    text = fake_rules.read_text()
    assert "Implemented 002 migration" in text
    assert "Finish working memory" in text  # still present


def test_get_progress_returns_entries(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "claude-code")
    tools.log_progress(kind="ask", content="Check capabilities")
    tools.log_progress(kind="done", content="Inventoried MCP tools")

    result = tools.get_progress()
    assert result["count"] == 2
    assert {e["kind"] for e in result["entries"]} == {"ask", "done"}


def test_end_progress_rotates_and_optionally_distills(hippo_env, monkeypatch):
    from hippocampus.mcp import tools
    from hippocampus.storage import fragments as F, ledger, sessions

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "opencode")
    tools.log_progress(kind="goal", content="Wire up OpenCode")
    tools.log_progress(kind="done", content="Added config path")

    out = tools.end_progress(distill_to_fragment=True, summary="OpenCode wiring complete", tags=["release"])
    assert out["rotated"] is True
    assert out["distilled_fragment"] is not None
    assert out["previous_session_id"] != out["new_session_id"]

    # Distilled fragment exists with correct summary + tags
    frag = F.get(out["distilled_fragment"]["id"])
    assert "release" in frag.tags
    assert "opencode" in frag.tags
    assert frag.summary == "OpenCode wiring complete"

    # New session is clean
    new_sid = out["new_session_id"]
    assert ledger.current_entries(new_sid) == []
