"""Integration: handoff file lifecycle through the MCP tools."""

from __future__ import annotations

from pathlib import Path


def test_log_progress_writes_handoff_and_echoes_goal(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    out = tools.log_progress(kind="goal", content="Ship handoff feature")
    assert out["logged"] is True
    assert out["goal"] == "Ship handoff feature"
    assert out["handoff_path"] is not None

    hpath = Path(out["handoff_path"])
    assert hpath.exists()
    body = hpath.read_text(encoding="utf-8")
    assert "Ship handoff feature" in body
    assert "- **status**: active" in body

    # Goal keeps echoing on later entries — the compaction anchor.
    out2 = tools.log_progress(kind="done", content="Wrote the module")
    assert out2["goal"] == "Ship handoff feature"
    body = hpath.read_text(encoding="utf-8")
    assert "Wrote the module" in body


def test_get_progress_includes_goal_and_handoff(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    tools.log_progress(kind="goal", content="G1")
    tools.log_progress(kind="goal", content="G2 revised")

    out = tools.get_progress()
    assert out["goal"] == "G2 revised"
    assert out["handoff_path"] is not None


def test_end_progress_finalizes_handoff(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    tools.log_progress(kind="goal", content="Finish it")
    tools.log_progress(kind="done", content="Did it")

    out = tools.end_progress(summary="Everything shipped")
    assert out["rotated"] is True
    assert out["handoff_path"] is not None
    body = Path(out["handoff_path"]).read_text(encoding="utf-8")
    assert "- **status**: completed" in body
    assert "Everything shipped" in body


def test_get_handoff_resumes_previous_session(hippo_env, monkeypatch):
    """After end_progress rotates, get_handoff falls back to the last session."""
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    tools.log_progress(kind="goal", content="Recoverable goal")
    ended = tools.end_progress()
    prev_sid = ended["previous_session_id"]

    out = tools.get_handoff()
    assert out["session_id"] == prev_sid
    assert out["exists"] is True
    assert out["goal"] == "Recoverable goal"
    assert "Recoverable goal" in out["content"]


def test_get_handoff_active_session(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "cursor")
    tools.log_progress(kind="goal", content="Active goal")
    out = tools.get_handoff()
    assert out["resumed_from_previous_session"] is False
    assert out["goal"] == "Active goal"
    assert out["exists"] is True


def test_handoff_disabled_setting(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    monkeypatch.setenv("HIPPO_HANDOFF_ENABLED", "false")
    out = tools.log_progress(kind="goal", content="No file please")
    assert out["logged"] is True
    assert out["handoff_path"] is None
    assert out["goal"] == "No file please"  # goal echo works regardless


def test_working_block_advertises_handoff_path(hippo_env, tmp_path, monkeypatch):
    from hippocampus.clients import registry
    from hippocampus.mcp import tools

    fake_rules = tmp_path / "fake_agents.md"
    fake_rules.write_text("# Fake Rules\n", encoding="utf-8")
    orig = registry.by_name("devin")
    fake_spec = type(orig)(
        name="devin",
        label="Devin CLI",
        rules_path=fake_rules,
        creation_header="# Fake",
        mcp_config_path=None,
        mcp_config_format="devin-json",
    )
    registry.CLIENTS[:] = [fake_spec if c.name == "devin" else c for c in registry.CLIENTS]
    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")

    out = tools.log_progress(kind="goal", content="Advertise the handoff")
    text = fake_rules.read_text(encoding="utf-8")
    assert "Handoff file" in text
    assert out["handoff_path"] in text
    assert "Compaction recovery" in text
