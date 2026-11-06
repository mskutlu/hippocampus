"""A2A store: server-side limits, dedupe, pause, ack, peer-turn seam."""

from __future__ import annotations

import pytest

A = "codex@mac"
B = "claude-code@mac/cc-1"


@pytest.fixture
def db(hippo_env, tmp_path):
    return tmp_path / "a2a.db"


def _send(db, mid, **kw):
    from hippocampus.a2a import store

    args = {"sender": A, "to": B, "content": "hi", "msg_id": mid}
    args.update(kw)
    return store.send(db, **args)


def _err(db, mid, **kw):
    from hippocampus.a2a.store import A2AError

    with pytest.raises(A2AError) as exc:
        _send(db, mid, **kw)
    return exc.value.status


def test_hop_computed_and_rejected(db):
    r0 = _send(db, "m0")
    assert r0["hop"] == 0
    prev = r0["msg_id"]
    for i in range(1, 4):
        r = _send(db, f"m{i}", in_reply_to=prev)
        assert r["hop"] == i and r["thread_id"] == r0["thread_id"]
        prev = r["msg_id"]
    assert _err(db, "m4", in_reply_to=prev) == 409


def test_reusing_thread_id_cannot_reset_hop(db):
    t = _send(db, "a")["thread_id"]
    assert _send(db, "b", thread_id=t)["hop"] == 1


def test_thread_cap(db, monkeypatch):
    from hippocampus import config

    monkeypatch.setattr(config, "get_setting", lambda k: {"a2a_max_thread_msgs": 3, "a2a_max_hops": 99}.get(k))
    t = _send(db, "t0")["thread_id"]
    _send(db, "t1", thread_id=t)
    _send(db, "t2", thread_id=t)
    assert _err(db, "t3", thread_id=t) == 409


def test_rate_limit(db, monkeypatch):
    from hippocampus import config

    monkeypatch.setattr(config, "get_setting", lambda k: {"a2a_rate_per_min": 2}.get(k))
    _send(db, "r0")
    _send(db, "r1")
    assert _err(db, "r2") == 429


def test_dedupe_is_idempotent(db):
    from hippocampus.a2a import store

    first = _send(db, "d0")
    assert _send(db, "d0") == first
    assert len(store.inbox(db, B)["messages"]) == 1


def test_size_cap(db):
    assert _err(db, "big", content="x" * (16 * 1024 + 1)) == 413


def test_pause_resume(db):
    from hippocampus.a2a import store

    _send(db, "p0")
    store.set_paused(db, True)
    assert _err(db, "p1") == 423
    assert store.inbox(db, B)["messages"] == []
    store.set_paused(db, False)
    assert len(store.inbox(db, B)["messages"]) == 1


def test_ack_first_wins_and_session_matching(db):
    from hippocampus.a2a import store

    _send(db, "k0", to="claude-code@mac")  # base address reaches any session, leased to the first poller
    assert len(store.inbox(db, "claude-code@mac/cc-1")["messages"]) == 1
    assert store.inbox(db, "claude-code@mac/cc-2")["messages"] == []
    assert store.ack(db, "claude-code@mac/cc-1", ["k0"]) == 1
    assert store.ack(db, "claude-code@mac/cc-2", ["k0"]) == 0
    assert store.inbox(db, "claude-code@mac/cc-2")["messages"] == []


def test_session_qualified_target_is_exclusive(db):
    from hippocampus.a2a import store

    _send(db, "x0", to="claude-code@mac/cc-1")
    assert store.inbox(db, "claude-code@mac/cc-2")["messages"] == []
    assert len(store.inbox(db, "claude-code@mac/cc-1")["messages"]) == 1


def test_audit_records_send_reject_deliver_ack(db):
    from hippocampus.a2a import store

    _send(db, "u0")
    _err(db, "u1", content="x" * 20000)
    store.inbox(db, B)
    store.ack(db, B, ["u0"])
    events = {e["event"] for e in store.audit(db)}
    assert {"send", "reject", "deliver", "ack"} <= events


def test_peer_turn_seam(db):
    from hippocampus.a2a import state

    assert state.active_peer_turn("k", db) is None
    state.begin_peer_turn("k", {"msg_id": "m", "from": A, "thread_id": "t", "hop": 1}, db)
    assert state.active_peer_turn("k", db)["msg_id"] == "m"
    state.end_peer_turn("k", db)
    assert state.active_peer_turn("k", db) is None


def test_device_address_leased_to_one_poller_then_expires(db, monkeypatch):
    from hippocampus.a2a import store

    _send(db, "l0", to="claude-code@mac")
    assert [m["msg_id"] for m in store.inbox(db, "claude-code@mac/cc-1")["messages"]] == ["l0"]
    assert store.inbox(db, "claude-code@mac/cc-2")["messages"] == []
    assert len(store.inbox(db, "claude-code@mac/cc-1")["messages"]) == 1  # the claimer sees it again until ack
    monkeypatch.setattr(store, "LEASE_SECONDS", -1)  # lease expired, still unacked: re-claimable
    assert [m["msg_id"] for m in store.inbox(db, "claude-code@mac/cc-2")["messages"]] == ["l0"]
    monkeypatch.setattr(store, "LEASE_SECONDS", 60)
    assert store.inbox(db, "claude-code@mac/cc-1")["messages"] == []  # cc-2 holds it now
    assert store.ack(db, "claude-code@mac/cc-1", ["l0"]) == 1  # ack stays first-wins, not claimer-only
    assert store.ack(db, "claude-code@mac/cc-2", ["l0"]) == 0


def test_lease_migration_on_old_db(db):
    import sqlite3

    from hippocampus.a2a import store

    old = sqlite3.connect(db)
    old.executescript(store._SCHEMA)  # pre-lease schema has no claimed_* columns
    old.execute("INSERT INTO a2a_messages(msg_id, thread_id, sender, recipient, content, hop, created_at) "
                "VALUES ('o1','t','codex@mac','claude-code@mac','hi',0,'2026-01-01T00:00:00.000000Z')")
    old.commit()
    old.close()
    assert [m["msg_id"] for m in store.inbox(db, "claude-code@mac/cc-1")["messages"]] == ["o1"]
    assert store.inbox(db, "claude-code@mac/cc-2")["messages"] == []
