"""Tests for V0.3: config file, shared mode, auto-tag, undo, idle auto-end."""

from __future__ import annotations

import time

import pytest


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------


def test_config_defaults(hippo_env):
    from hippocampus import config

    s = config.all_settings()
    assert s["working_block_mode"] == "per_client"
    assert s["auto_end_idle_minutes"] is None


def test_config_set_persists(hippo_env):
    from hippocampus import config

    config.set_setting("working_block_mode", "shared")
    assert config.get_setting("working_block_mode") == "shared"
    assert config.config_path().exists()


def test_config_env_overrides_file(hippo_env, monkeypatch):
    from hippocampus import config

    config.set_setting("auto_end_idle_minutes", 30)
    monkeypatch.setenv("HIPPO_AUTO_END_IDLE_MINUTES", "5")
    assert config.get_setting("auto_end_idle_minutes") == 5


# ---------------------------------------------------------------------------
# Auto-tag fragments referenced in log_progress
# ---------------------------------------------------------------------------


def test_log_progress_boosts_referenced_fragment(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    stored = tools.remember(content="Kafka consumers must be idempotent.")
    fid = stored["fragment"]["id"]

    before = tools.get_fragment(fid, boost_on_read=False)["fragment"]["confidence"]
    out = tools.log_progress(
        kind="done",
        content=f"Verified idempotent consumer invariant {fid} holds.",
    )
    assert out["logged"] is True
    assert fid in out["boosted_fragments"]
    after = tools.get_fragment(fid, boost_on_read=False)["fragment"]["confidence"]
    assert after > before  # boosted
    assert any(t.startswith("log_progress:done") for t in tools.get_fragment(fid, boost_on_read=False)["fragment"]["tags"])


def test_log_progress_ignores_unknown_fragment_id(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    out = tools.log_progress(
        kind="ask",
        content="User mentioned frag_01XXXXXXXXXXXXXXXXXXXXXXXX which doesn't exist.",
    )
    # Does not raise; unknown id is simply skipped
    assert out["logged"] is True
    assert out["boosted_fragments"] == []


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


def test_undo_pops_latest_entry(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "claude-code")
    tools.log_progress(kind="goal", content="Ship V0.3")
    tools.log_progress(kind="ask", content="Add undo")
    tools.log_progress(kind="done", content="Wrote the tool")

    out = tools.undo_last_entry()
    assert out["undone"] is True
    assert out["entry"]["content"] == "Wrote the tool"

    remaining = tools.get_progress()
    assert [e["content"] for e in remaining["entries"]] == ["Ship V0.3", "Add undo"]


def test_undo_refuses_old_entries(hippo_env, monkeypatch):
    from hippocampus.mcp import tools
    from hippocampus.storage.db import get_conn

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "claude-code")
    tools.log_progress(kind="goal", content="Old thing")
    # Backdate the entry
    with get_conn() as conn:
        conn.execute(
            "UPDATE session_ledger SET created_at = '2020-01-01T00:00:00.000Z'"
        )

    out = tools.undo_last_entry()
    assert out["undone"] is False
    assert out["reason"] == "entry_too_old"


def test_undo_without_session(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "fresh-client")
    out = tools.undo_last_entry()
    assert out["undone"] is False
    assert out["reason"] == "no_active_session"


# ---------------------------------------------------------------------------
# Shared mode
# ---------------------------------------------------------------------------


def test_shared_mode_latest_session_picker(hippo_env, monkeypatch):
    from hippocampus.mcp import tools
    from hippocampus.storage import ledger

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    tools.log_progress(kind="goal", content="devin goal")
    time.sleep(0.01)
    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "claude-code")
    tools.log_progress(kind="goal", content="claude goal")

    latest = ledger.latest_session_across_clients()
    assert latest is not None
    sid, client, _ = latest
    assert client == "claude-code"


# ---------------------------------------------------------------------------
# Idle auto-end
# ---------------------------------------------------------------------------


def test_auto_end_noop_when_disabled(hippo_env):
    from hippocampus.mcp import tools

    out = tools.auto_end_idle_sessions()
    assert out["ended"] == 0
    assert out["reason"] == "disabled"


def test_auto_end_rotates_idle_sessions(hippo_env, monkeypatch):
    from hippocampus import config
    from hippocampus.mcp import tools
    from hippocampus.storage import sessions as sessions_store
    from hippocampus.storage.db import get_conn

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    tools.log_progress(kind="goal", content="Stale thing")
    before_sid = sessions_store.current_session_id("devin", open_if_missing=False)

    # Backdate all session_ledger rows + session start so the session looks idle
    with get_conn() as conn:
        conn.execute("UPDATE session_ledger SET created_at = '2020-01-01T00:00:00.000Z'")
        conn.execute("UPDATE sessions SET started_at = '2020-01-01T00:00:00.000Z'")

    config.set_setting("auto_end_idle_minutes", 30)
    out = tools.auto_end_idle_sessions()
    assert out["ended"] == 1
    assert out["sessions"][0]["session_id"] == before_sid

    # A new session pointer was installed
    after_sid = sessions_store.current_session_id("devin", open_if_missing=False)
    assert after_sid != before_sid
