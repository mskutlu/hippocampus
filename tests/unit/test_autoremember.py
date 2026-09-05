"""V9 W3 — autoremember pattern detection + persistence."""

from __future__ import annotations

import pytest


# -----------------------------
# Phrase detector
# -----------------------------

def test_detects_each_trigger_phrase(hippo_env, monkeypatch):
    from hippocampus.dynamics import autoremember

    monkeypatch.setenv("HIPPO_AUTOREMEMBER_MIN_CHARS", "10")

    phrases = [
        "Please remember that anaconda3 binaries are blocked from local network — use SSH tunnels.",
        "Always run hippo doctor after any setting change to verify the rules file got the WORKING block.",
        "Never push Docker images locally; merge to main and let GitLab CI build and deploy them.",
        "Don't forget to bump CHANGELOG before pushing a release tag.",
        "From now on, all temporary delegations must validate source AND target inputs.",
        "Next time you touch the kubectl router, also update the SSH tunnel section in the runbook.",
        "Add this to global rules: never use kubectl on globex repos.",
        "Add it to the global rules so I don't have to repeat: macOS Tahoe blocks anaconda.",
        "Keep in mind the customer count: customer-a, customer-b, customer-c, customer-d, customer-e.",
        "Make sure you run pytest before every commit, even for doc changes.",
    ]
    for p in phrases:
        rule = autoremember.detect(p)
        assert rule is not None, f"Expected trigger in: {p}"
        assert rule.sentence
        assert rule.summary.lower().startswith("rule (")


def test_no_trigger_returns_none(hippo_env, monkeypatch):
    from hippocampus.dynamics import autoremember

    monkeypatch.setenv("HIPPO_AUTOREMEMBER_MIN_CHARS", "10")
    samples = [
        "What is the status of the build?",
        "Run the tests and fix the failures.",
        "How do I deploy to customer-a?",
    ]
    for s in samples:
        assert autoremember.detect(s) is None


def test_min_chars_guard(hippo_env, monkeypatch):
    from hippocampus.dynamics import autoremember

    monkeypatch.setenv("HIPPO_AUTOREMEMBER_MIN_CHARS", "60")
    # Has trigger but too short -> rejected
    assert autoremember.detect("always do X") is None


# -----------------------------
# Persistence
# -----------------------------

def test_auto_remember_persists_fragment(hippo_env, monkeypatch):
    from hippocampus.dynamics import autoremember
    from hippocampus.storage import fragments as F

    monkeypatch.setenv("HIPPO_AUTOREMEMBER_MIN_CHARS", "10")
    prompt = "Always run pytest before commit, even for doc-only changes."
    frag = autoremember.auto_remember_from_prompt(prompt, client="devin")
    assert frag is not None
    assert "auto-remembered" in frag["tags"]
    assert "devin" in frag["tags"]
    assert not any(":" in t for t in frag["tags"])
    assert frag["source_type"] == "auto-remembered"


def test_auto_remember_dedups_identical_summary(hippo_env, monkeypatch):
    from hippocampus.dynamics import autoremember

    monkeypatch.setenv("HIPPO_AUTOREMEMBER_MIN_CHARS", "10")
    prompt = "Always run pytest before commit, even for doc-only changes."
    first = autoremember.auto_remember_from_prompt(prompt, client="devin")
    assert first is not None
    second = autoremember.auto_remember_from_prompt(prompt, client="devin")
    assert second is None


def test_auto_remember_disabled_flag(hippo_env, monkeypatch):
    from hippocampus.dynamics import autoremember

    monkeypatch.setenv("HIPPO_AUTOREMEMBER_ENABLED", "false")
    monkeypatch.setenv("HIPPO_AUTOREMEMBER_MIN_CHARS", "10")
    prompt = "Always run pytest before commit."
    frag = autoremember.auto_remember_from_prompt(prompt, client="devin")
    assert frag is None


# -----------------------------
# MCP tool surface
# -----------------------------

def test_auto_remember_tool_returns_status(hippo_env, monkeypatch):
    from hippocampus.mcp import tools

    monkeypatch.setenv("HIPPO_AUTOREMEMBER_MIN_CHARS", "10")
    out = tools.auto_remember(
        "Always run hippo doctor after any setting change to verify everything wired.",
        client="devin",
    )
    assert out["remembered"] is True
    assert out["fragment"] is not None
    assert out["trigger"].startswith("always")


# -----------------------------
# V11 — hook envelopes never become rules
# -----------------------------

ENVELOPES = [
    "<task-notification> <task-id>ab05</task-id> <summary>Monitor event: never mind</summary> <event>NOT COMPLETE, make sure you check again</event>",
    "  <system-reminder>Always respond in English. Never translate.</system-reminder>",
    "<task-notification>\n<task-id>b60r</task-id>\n<summary>RunAI resume #3 verdicts</summary>\n</task-notification>",
    "Session summary: <task-notification> <task-id>bwnm</task-id> always be careful",
]


def test_envelopes_are_ignored(hippo_env, monkeypatch):
    from hippocampus.dynamics import autoremember

    monkeypatch.setenv("HIPPO_AUTOREMEMBER_MIN_CHARS", "10")
    for text in ENVELOPES:
        assert autoremember.is_envelope(text)
        assert autoremember.detect(text) is None, text


def test_long_or_tagged_sentence_is_ignored(hippo_env, monkeypatch):
    from hippocampus.dynamics import autoremember

    monkeypatch.setenv("HIPPO_AUTOREMEMBER_MIN_CHARS", "10")
    long_sentence = "Never " + "x" * 400 + "."
    assert autoremember.detect(long_sentence) is None
    tagged = "Please always run <b>hippo doctor</b> after any setting change to verify."
    assert autoremember.detect(tagged) is None
    plain = "Please always run hippo doctor after any setting change to verify."
    assert autoremember.detect(plain) is not None
