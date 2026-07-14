"""Transcript capture is separate from distilled long-term fragments."""

from __future__ import annotations


def test_log_transcript_and_progress_share_session_context(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "codex")
    monkeypatch.setenv("HIPPO_TRANSCRIPT_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("HIPPOCAMPUS_TTY", "/dev/ttys123")
    monkeypatch.setenv("HIPPOCAMPUS_CWD", "/repo/a")

    raw = tools.log_transcript(
        role="user",
        content="full raw prompt body",
        source_event="UserPromptSubmit",
    )
    assert raw["logged"] is True

    monkeypatch.setenv("HIPPOCAMPUS_TRANSCRIPT_PROMPT_LOGGED", "1")
    tools.log_progress(kind="ask", content="full raw prompt body")
    monkeypatch.delenv("HIPPOCAMPUS_TRANSCRIPT_PROMPT_LOGGED")
    tools.log_progress(kind="decision", content="Use scoped session keys")

    out = tools.get_transcript()
    assert out["count"] == 2
    assert [e["role"] for e in out["entries"]] == ["user", "reasoning_summary"]
    assert out["entries"][0]["content"] == "full raw prompt body"


def test_log_transcript_dedups_recent_identical_rows(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "devin")
    monkeypatch.setenv("HIPPO_TRANSCRIPT_CAPTURE_ENABLED", "true")
    first = tools.log_transcript(role="assistant", content="visible answer")
    second = tools.log_transcript(role="assistant", content="visible answer")

    assert first["logged"] is True
    assert second["logged"] is False
