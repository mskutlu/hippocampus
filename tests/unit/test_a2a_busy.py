"""A2A busy gating: channel pushes never land in a Claude Code session mid-turn; trust-aware guard deny text."""

from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

ME = "claude-code@dev/cc-s1"
PEER = "codex@peer/x"


@pytest.fixture
def a2a_on(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPOCAMPUS_A2A_ENABLED", "1")
    monkeypatch.delenv("HIPPOCAMPUS_A2A_DB", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    for k in ("sync_url", "sync_token"):
        monkeypatch.delenv("HIPPO_" + k.upper(), raising=False)


@pytest.fixture
def pushed(a2a_on, monkeypatch):
    """A real channel Pusher for cc-s1 wired into the poller's ON_MESSAGE; yields the list of sent notifications."""
    from hippocampus.a2a import channel, tools

    class Stream:
        sent: list = []

        async def send(self, m):
            self.sent.append(m)

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    stream = Stream()
    stream.sent = []
    monkeypatch.setattr(tools, "ON_MESSAGE", [channel.Pusher(loop, stream, "cc-s1").on_message])
    yield stream.sent
    loop.call_soon_threadsafe(loop.stop)


def _box():
    from hippocampus.a2a import client

    return client.Mailbox(client.local_transport())


def _delivered(msg_id: str) -> bool:
    from hippocampus.a2a import store

    conn = sqlite3.connect(store.db_path())
    try:
        return conn.execute("SELECT delivered_at FROM a2a_messages WHERE msg_id = ?", (msg_id,)).fetchone()[0] is not None
    finally:
        conn.close()


def test_busy_session_defers_then_pushes_once(pushed):
    from hippocampus.a2a import guard, state, tools

    box = _box()
    mid = box.send(PEER, ME, "q?")["msg_id"]
    guard.prompt({"session_id": "s1"}, {})  # UserPromptSubmit
    assert tools._poll_once(box, ME, 0, 0, "cc-s1") == 0
    assert pushed == [] and state.active_peer_turn("cc-s1") is None and not _delivered(mid)

    guard.stop({"session_id": "s1"}, {})  # user turn over
    tools._poll_once(box, ME, 0, 0, "cc-s1")
    assert len(pushed) == 1 and _delivered(mid) and state.active_peer_turn("cc-s1")["msg_id"] == mid

    mid2 = box.send(PEER, ME, "second")["msg_id"]
    tools._poll_once(box, ME, 0, 0, "cc-s1")  # the peer turn the push started also defers
    assert len(pushed) == 1 and not _delivered(mid2)
    guard.stop({"session_id": "s1"}, {})
    for _ in range(2):
        tools._poll_once(box, ME, 0, 0, "cc-s1")
    assert len(pushed) == 2


def test_turn_starting_during_long_poll_leaves_message_unacked(pushed):
    from hippocampus.a2a import client, state, tools

    inner = client.local_transport()

    def transport(method, route, body):
        res = inner(method, route, body)
        if route.startswith("/v1/a2a/inbox"):
            state.mark_busy("cc-s1")  # the user submitted a prompt while we were long-polling
        return res

    box = client.Mailbox(transport)
    mid = box.send(PEER, ME, "q?")["msg_id"]
    assert tools._poll_once(box, ME, 0, 0, "cc-s1") == 0
    assert pushed == [] and not _delivered(mid)
    state.end_turn("cc-s1")
    tools._poll_once(_box(), ME, 0, 0, "cc-s1")
    assert len(pushed) == 1 and _delivered(mid)


def test_busy_ttl_releases(pushed, monkeypatch):
    from hippocampus.a2a import state, tools

    box = _box()
    box.send(PEER, ME, "q?")
    state.mark_busy("cc-s1")  # a turn whose Stop never ran
    tools._poll_once(box, ME, 0, 0, "cc-s1")
    assert pushed == [] and state.busy("cc-s1")
    monkeypatch.setenv("HIPPO_A2A_PEER_TURN_TTL_S", "0")
    assert not state.busy("cc-s1")
    tools._poll_once(box, ME, 0, 0, "cc-s1")
    assert len(pushed) == 1


def test_device_level_message_goes_to_idle_session_once(a2a_on, monkeypatch):
    from hippocampus.a2a import client, state, tools

    got: list[tuple[str, str]] = []
    who = {}
    a, b, base = "claude-code@dev/cc-a", "claude-code@dev/cc-b", "claude-code@dev"
    monkeypatch.setattr(tools, "ON_MESSAGE", [lambda m: got.append((who["key"], m["msg_id"]))])

    def poll(addr, key, box=None):
        who["key"] = key
        tools._poll_once(box or _box(), addr, 0, 0, key)

    # A busy before polling: it claims nothing, idle B takes it.
    mid = _box().send(PEER, base, "anyone?")["msg_id"]
    state.mark_busy("cc-a")
    poll(a, "cc-a")
    poll(b, "cc-b")
    state.end_turn("cc-a")
    poll(a, "cc-a")
    poll(b, "cc-b")
    assert got == [("cc-b", mid)]

    # A goes busy mid-poll: its claim holds for the lease, then A (idle) delivers it exactly once.
    got.clear()
    mid2 = _box().send(PEER, base, "again?")["msg_id"]
    inner = client.local_transport()

    def transport(method, route, body):
        res = inner(method, route, body)
        if route.startswith("/v1/a2a/inbox"):
            state.mark_busy("cc-a")
        return res

    poll(a, "cc-a", client.Mailbox(transport))
    poll(b, "cc-b")  # A's lease still holds
    assert got == []
    state.end_turn("cc-a")
    poll(a, "cc-a")
    poll(b, "cc-b")
    poll(a, "cc-a")
    assert got == [("cc-a", mid2)]


def test_guard_text_own_device_vs_unknown(a2a_on, tmp_path):
    from hippocampus.a2a import guard, state

    bash = {"session_id": "s1", "cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "ls"}}
    state.begin_peer_turn("cc-s1", {"msg_id": "m1", "from": PEER, "thread_id": "t", "hop": 0}, trust="own_device")
    own = guard.pretooluse(bash, {})
    assert "untrusted" not in own and 'reply(msg_id="m1"' in own and "turn ends" in own
    assert "not SendMessage" in own
    assert "user can" not in own and "policy set" not in own

    state.begin_peer_turn("cc-s1", {"msg_id": "m2", "from": PEER, "thread_id": "t", "hop": 0}, trust="unknown")
    unknown = guard.pretooluse(bash, {})
    assert "tell the user a message is waiting" in unknown and "reply(" not in unknown


def test_pusher_stores_trust_for_guard(pushed):
    from hippocampus.a2a import state, tools
    from hippocampus.sync import client as sync_client

    _box().send(f"codex@{sync_client.device_id()}/o", ME, "hi")
    tools._poll_once(_box(), ME, 0, 0, "cc-s1")
    assert state.active_peer_turn("cc-s1")["trust"] == "own_device"
