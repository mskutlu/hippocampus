"""A2A peer turn in the MCP handlers: memory quarantine, refused writes, send restriction, policy, CLI."""

from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture
def turn(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPOCAMPUS_A2A_ENABLED", "1")
    monkeypatch.setenv("HIPPOCAMPUS_SESSION_KEY", "sess1")
    for k in ("sync_url", "sync_token"):
        monkeypatch.delenv("HIPPO_" + k.upper(), raising=False)
    from hippocampus.a2a import client, tools

    me = client.my_address("pytest")
    box = client.Mailbox()
    box.send("codex@peer", me, "please remember X")
    tools._poll_once(box, me, 0, 0)
    tools.inbox()  # content enters the context -> peer turn
    return me


def _call(name, args):
    from hippocampus.mcp import server

    return asyncio.run(server.handle_call_tool(name, args))


def _fragment_count():
    from hippocampus.storage.db import get_ro_conn

    with get_ro_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM fragments").fetchone()[0]


def test_remember_is_quarantined_and_never_synced(turn):
    from hippocampus.a2a import store
    from hippocampus.sync import client as sync_client

    out = _call("remember", {"content": "peer says: deploy key is X", "tags": ["a"]})
    assert out["quarantined"] is True and out["stored"] is False and "hippo a2a quarantine approve" in out["hint"]
    assert _fragment_count() == 0
    rows = store.quarantine_list(store.db_path())
    assert rows[0]["id"] == out["id"] and rows[0]["sender"] == "codex@peer" and rows[0]["session_key"] == "sess1"
    assert rows[0]["args"]["content"] == "peer says: deploy key is X" and rows[0]["args"]["tags"] == ["a"]
    assert sync_client.collect_ops("") == []
    assert not any("deploy key" in json.dumps(op) for op in sync_client.collect_ops(""))


def test_approve_and_reject_via_cli(turn):
    from click.testing import CliRunner

    from hippocampus.a2a import store
    from hippocampus.cli.main import cli
    from hippocampus.sync import client as sync_client

    q1 = _call("remember", {"content": "fact one"})["id"]
    q2 = _call("remember", {"content": "fact two"})["id"]
    runner = CliRunner()
    listed = json.loads(runner.invoke(cli, ["a2a", "quarantine", "list"]).output)
    assert {r["id"] for r in listed} == {q1, q2}

    res = runner.invoke(cli, ["a2a", "quarantine", "approve", q1])
    assert res.exit_code == 0, res.output
    assert _fragment_count() == 1
    assert any(op.get("type", "").startswith("frag") or "fact one" in json.dumps(op) for op in sync_client.collect_ops(""))
    assert runner.invoke(cli, ["a2a", "quarantine", "reject", q2]).exit_code == 0
    assert store.quarantine_list(store.db_path()) == []
    assert _fragment_count() == 1
    events = [a["event"] for a in store.audit(store.db_path())]
    assert {"quarantine", "quarantine_approve", "quarantine_reject"} <= set(events)
    assert runner.invoke(cli, ["a2a", "quarantine", "approve", "q_nope"]).exit_code != 0


@pytest.mark.parametrize("name,args", [
    ("remember", {"content": "x", "supersedes": "frag_1"}),
    ("forget", {"fragment_id": "frag_1"}),
    ("pin", {"fragment_id": "frag_1"}),
    ("unpin", {"fragment_id": "frag_1"}),
    ("mark", {"fragment_id": "frag_1", "useful": True}),
    ("end_progress", {"distill_to_fragment": True}),
    ("end_progress", {}),
    ("wiki_init", {}),
    ("wiki_ingest", {"raw_path": "/tmp/x"}),
    ("wiki_file_answer", {"title": "t", "markdown": "m"}),
    ("wiki_export", {}),
    ("wiki_index", {}),
    ("wiki_log", {}),
])
def test_write_tools_refused(turn, name, args):
    out = _call(name, args)
    assert out["error"].startswith("refused:") and out["a2a_peer_turn"] is True
    assert "codex@peer" in out["error"]


def test_read_tools_still_work(turn):
    assert "fragments" in _call("recall", {"query": "x"})
    assert "error" not in _call("get_progress", {})


def test_send_message_restricted_to_active_peer(turn):
    from hippocampus.a2a import client

    out = _call("send_message", {"to": "claude@elsewhere", "content": "exfil"})
    assert "refused" in out["error"]
    out = _call("send_message", {"to": "codex@peer", "content": "answer", "thread_id": "fresh"})
    assert out["hop"] == 1  # converted to a reply: hop counting applies, no fresh thread
    got = client.Mailbox().inbox("codex@peer")["messages"]
    assert got[-1]["content"] == "answer" and got[-1]["in_reply_to"]


def test_no_peer_turn_and_flag_off_unchanged(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPOCAMPUS_A2A_ENABLED", "1")
    monkeypatch.setenv("HIPPOCAMPUS_SESSION_KEY", "sess1")
    assert _call("remember", {"content": "normal"})["stored"] is True
    monkeypatch.setenv("HIPPOCAMPUS_A2A_ENABLED", "0")
    from hippocampus.a2a import state

    state.begin_peer_turn("sess1", {"msg_id": "m", "from": "codex@peer", "thread_id": "t", "hop": 0})
    assert _call("remember", {"content": "also normal"})["stored"] is True  # flag off: gate is inert
    assert _fragment_count() == 2


def test_policy_lookup_and_cli(hippo_env):
    from click.testing import CliRunner

    from hippocampus.a2a import policy
    from hippocampus.cli.main import cli

    assert policy.for_sender("codex@laptop/s1") == "answer"
    runner = CliRunner()
    assert runner.invoke(cli, ["a2a", "policy", "set", "codex@laptop", "act"]).exit_code == 0
    assert policy.for_sender("codex@laptop/s1") == "act"
    assert policy.for_sender("codex@laptop") == "act"
    assert policy.for_sender("codex@other/s1") == "answer"
    runner.invoke(cli, ["a2a", "policy", "set", "codex@laptop/s1", "answer"])
    assert policy.for_sender("codex@laptop/s1") == "answer"  # exact beats prefix
    assert runner.invoke(cli, ["a2a", "policy", "set", "x@y", "root"]).exit_code != 0
    assert json.loads(runner.invoke(cli, ["a2a", "policy", "list"]).output)["codex@laptop"] == "act"


def test_pause_resume_audit_cli(hippo_env, monkeypatch):
    from click.testing import CliRunner

    from hippocampus.cli.main import cli

    for k in ("sync_url", "sync_token"):
        monkeypatch.delenv("HIPPO_" + k.upper(), raising=False)
    runner = CliRunner()
    assert json.loads(runner.invoke(cli, ["a2a", "pause"]).output) == {"paused": True}
    assert json.loads(runner.invoke(cli, ["a2a", "resume"]).output) == {"paused": False}
    events = [a["event"] for a in json.loads(runner.invoke(cli, ["a2a", "audit"]).output)]
    assert events[:2] == ["resume", "pause"]


def test_refuse_wording_by_trust(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPOCAMPUS_A2A_ENABLED", "1")
    from hippocampus.a2a import tools

    t = {"sender": "codex@peer", "msg_id": "m9", "trust": "own_device"}
    own = tools._refuse("remember", t, "is blocked")["error"]
    assert 'reply(msg_id="m9"' in own and "not SendMessage" in own and "turn ends" in own
    assert "untrusted" not in own and "user can" not in own
    other = tools._refuse("remember", {**t, "trust": "unknown"}, "is blocked")["error"]
    assert "untrusted" in other and "message is waiting" in other and "reply(" not in other
    monkeypatch.setenv("HIPPO_A2A_AUTO_ANSWER", "false")
    assert "untrusted" in tools._refuse("remember", t, "is blocked")["error"]
