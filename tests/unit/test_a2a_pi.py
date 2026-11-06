"""Pi tool lockdown: hidden a2a_check_tool / a2a_end_turn, gating, pi tool-name mapping."""

from __future__ import annotations

import asyncio

import pytest

TURN = {"session_key": "sess1", "msg_id": "m1", "sender": "codex@peer/x", "thread_id": "t", "hop": 0}


@pytest.fixture
def pi_on(hippo_env, monkeypatch, tmp_path):
    monkeypatch.setenv("HIPPOCAMPUS_A2A_ENABLED", "1")
    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "pi")
    monkeypatch.setenv("HIPPOCAMPUS_SESSION_KEY", "sess1")
    for k in ("sync_url", "sync_token"):
        monkeypatch.delenv("HIPPO_" + k.upper(), raising=False)
    p = tmp_path / "home" / "proj"
    (p / "src").mkdir(parents=True)
    (p / "src" / "a.py").write_text("x")
    (p / ".env").write_text("T=1")
    return p


def _call(name, args):
    from hippocampus.mcp import server

    return asyncio.run(server.handle_call_tool(name, args))


def test_map_tool():
    from hippocampus.a2a.pi import map_tool

    assert map_tool("read", {"path": "a", "offset": 3}) == ("Read", {"file_path": "a"})
    assert map_tool("grep", {"pattern": "x", "path": "f", "glob": "*.py", "limit": 1}) == ("Grep", {"path": "f", "glob": "*.py"})
    assert map_tool("find", {"pattern": "*.py"}) == ("Glob", {"pattern": "*.py"})
    assert map_tool("ls", {}) == ("LS", {})
    assert map_tool("recall", {"query": "q"})[0] == "mcp__hippocampus__recall"
    for denied in ("bash", "edit", "write", "powershell", "whatever"):
        assert map_tool(denied, {"command": "x"})[0] == denied


def test_check_and_end_turn(pi_on):
    from hippocampus.a2a import state

    p = pi_on
    ok = lambda tool, inp: _call("a2a_check_tool", {"tool_name": tool, "tool_input": inp, "cwd": str(p)})
    assert ok("bash", {"command": "ls"}) == {"allow": True}  # no peer turn
    state.begin_peer_turn("sess1", {"msg_id": "m1", "from": "codex@peer/x", "thread_id": "t", "hop": 0})
    assert ok("read", {"path": "src/a.py"}) == {"allow": True}
    assert ok("ls", {"path": "src"}) == {"allow": True}
    assert ok("recall", {"query": "q"}) == {"allow": True}
    for tool, inp in [("bash", {"command": "ls"}), ("write", {"path": "x"}), ("edit", {"path": "x"}),
                      ("read", {"path": ".env"}), ("read", {"path": "/etc/passwd"}), ("remember", {"content": "x"})]:
        res = ok(tool, inp)
        assert res["allow"] is False and "A2A peer-turn restriction" in res["reason"], (tool, res)
    assert _call("a2a_end_turn", {}) == {"ended": True}
    assert state.active_peer_turn("sess1") is None
    assert ok("bash", {"command": "ls"}) == {"allow": True}


def test_act_policy_allows(pi_on, monkeypatch):
    from hippocampus.a2a import policy, state

    policy.set_for("codex@peer", "act")
    state.begin_peer_turn("sess1", {"msg_id": "m1", "from": "codex@peer/x", "thread_id": "t", "hop": 0})
    assert _call("a2a_check_tool", {"tool_name": "bash", "tool_input": {}, "cwd": str(pi_on)}) == {"allow": True}


def test_hidden_tools_gated_to_pi(pi_on, monkeypatch):
    from hippocampus.mcp import server

    assert "a2a_check_tool" not in {t.name for t in server.TOOL_SPECS}
    assert "a2a_check_tool" not in {t["name"] for t in server.tools_manifest()}
    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "claude-code")
    for name in ("a2a_check_tool", "a2a_end_turn"):
        with pytest.raises(ValueError, match="unknown tool"):
            _call(name, {})


def test_disabled_allows(pi_on, monkeypatch):
    monkeypatch.delenv("HIPPOCAMPUS_A2A_ENABLED")
    assert _call("a2a_check_tool", {"tool_name": "bash", "tool_input": {}, "cwd": "/"}) == {"allow": True}
