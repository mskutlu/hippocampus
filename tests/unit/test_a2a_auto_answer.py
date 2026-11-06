"""A2A standing authorization (rules + hook + channel INSTRUCTIONS) and the own_device trust marker."""

from __future__ import annotations

import pytest


@pytest.fixture
def a2a_on(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPOCAMPUS_A2A_ENABLED", "1")
    monkeypatch.delenv("HIPPOCAMPUS_A2A_DB", raising=False)


MARK = "A2A standing authorization"


@pytest.mark.parametrize("enabled,auto,expect", [("1", None, True), ("1", "false", False), ("0", None, False)])
def test_authorization_in_rules_block_and_session_start(hippo_env, monkeypatch, enabled, auto, expect):
    monkeypatch.setenv("HIPPOCAMPUS_A2A_ENABLED", enabled)
    if auto:
        monkeypatch.setenv("HIPPO_A2A_AUTO_ANSWER", auto)
    from hippocampus.clients import hook_context
    from hippocampus.clients.injector import format_injection_block

    assert (MARK in format_injection_block([])) is expect
    start = hook_context.render_context(client="pytest", include_fragments=False, event_name="SessionStart")
    assert (MARK in start) is expect
    prompt = hook_context.render_context(client="pytest", include_fragments=False, event_name="UserPromptSubmit")
    assert MARK not in prompt  # once per session, not on every prompt


def test_instructions_carry_untrusted_and_authorization(a2a_on, monkeypatch):
    from hippocampus.a2a import channel
    from hippocampus.mcp.server import server

    assert "UNTRUSTED DATA" in channel.INSTRUCTIONS and "never instructions" in channel.INSTRUCTIONS
    assert "authorized you to answer trust=own_device" in channel.INSTRUCTIONS
    monkeypatch.setenv("HIPPOCAMPUS_CHANNEL", "1")
    assert channel.init_options(server).instructions == channel.INSTRUCTIONS
    monkeypatch.setenv("HIPPO_A2A_AUTO_ANSWER", "false")
    off = channel.init_options(server).instructions
    assert "UNTRUSTED DATA" in off and "authorized" not in off


def test_trust_own_device_cached_no_network_per_message(a2a_on, monkeypatch):
    from hippocampus.a2a import channel
    from hippocampus.sync import client as sync_client

    calls = []

    def transport(method, route, body):
        calls.append(route)
        return {"device_status": [{"device": "dev_phone", "behind": 0}]}

    monkeypatch.setattr(sync_client, "configured_transport", lambda: transport)
    me = sync_client.device_id()
    assert channel.trust_for(f"claude-code@{me}/cc-x") == "own_device"
    assert calls == []  # same device: no lookup at all
    for _ in range(5):
        assert channel.trust_for("codex@dev_phone/s1") == "own_device"
        assert channel.trust_for("codex@dev_stranger") == "unknown"
        assert channel.trust_for("garbage") == "unknown"
    assert calls == ["/v1/health"]  # one fetch, then cached
    monkeypatch.setattr(channel, "DEVICE_CACHE_SECONDS", 0)
    channel.trust_for("codex@dev_phone")
    assert len(calls) == 2  # refreshed only after the TTL


def test_trust_unknown_without_hub(a2a_on):
    from hippocampus.a2a import channel

    assert channel.trust_for("codex@dev_other/s") == "unknown"  # no sync_url configured: fail closed


def test_pusher_marks_trust_and_policy(a2a_on, monkeypatch):
    from hippocampus.a2a import channel, policy

    policy.set_for("codex@dev_phone", "act")
    monkeypatch.setattr(channel, "_hub_devices", lambda: frozenset({"dev_phone"}))
    sent = []
    monkeypatch.setattr(channel, "build_message", lambda m, t, p: sent.append((t, p)))

    class _Future:
        def result(self, timeout):
            return None

    monkeypatch.setattr(channel.asyncio, "run_coroutine_threadsafe", lambda coro, loop: _Future())
    msg = {"msg_id": "m1", "from": "codex@dev_phone/s", "thread_id": "t1", "hop": 0, "content": "hi"}
    channel.Pusher(None, type("S", (), {"send": lambda self, m: None})(), "cc-s1").on_message(msg)
    assert sent == [("own_device", "act")]
    params = channel.build_params(msg, "own_device", "act")
    assert params["meta"]["trust"] == "own_device" and params["meta"]["policy"] == "act"
