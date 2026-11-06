"""A2A Claude Code guard: PreToolUse decision, session-key mapping, Stop, hook script end to end."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TURN = {"session_key": "cc-s1", "msg_id": "m1", "sender": "codex@peer/x", "thread_id": "t", "hop": 0}


@pytest.fixture
def proj(tmp_path):
    home = tmp_path / "home"
    p = home / "work" / "proj"
    (p / "src").mkdir(parents=True)
    (p / "src" / "a.py").write_text("x")
    (p / ".env").write_text("TOKEN=1")
    (p / "config").mkdir()
    (p / "config" / "credentials.yml").write_text("k")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("k")
    (tmp_path / "outside.txt").write_text("o")
    (p / "link_out").symlink_to(tmp_path / "outside.txt")
    (p / "innocent").symlink_to(p / ".env")
    return home, p


def _decide(tool, inp, p, home, *, turn=TURN, policy="answer", cwd=None):
    from hippocampus.a2a import guard

    return guard.decide(tool, inp, str(cwd or p), str(p), turn, policy, home)


@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": "src/a.py"}),
    ("Read", {"file_path": "{p}/src/a.py"}),
    ("Grep", {"pattern": "x", "path": "src/a.py"}),
    ("Grep", {"pattern": "x", "path": "{p}/src/a.py", "glob": "*.py"}),
    ("mcp__plugin_hippocampus_hippocampus__recall", {"query": "q"}),
    ("mcp__plugin_hippocampus_hippocampus__inbox", {}),
    ("mcp__plugin_hippocampus_hippocampus__reply", {}),
    ("Glob", {"pattern": "**/*.py"}),
    ("LS", {"path": "{p}/src"}),
    ("mcp__hippocampus__recall", {"query": "q"}),
    ("mcp__hippocampus__get_fragment", {}),
    ("mcp__hippocampus__list_fragments", {}),
    ("mcp__hippocampus__top_fragments", {}),
    ("mcp__hippocampus__inbox", {}),
    ("mcp__hippocampus__reply", {}),
    ("mcp__hippocampus__list_agents", {}),
    ("mcp__hippocampus__get_progress", {}),
    ("mcp__hippocampus__log_progress", {}),
])
def test_allowed(proj, tool, inp):
    home, p = proj
    inp = {k: v.replace("{p}", str(p)) if isinstance(v, str) else v for k, v in inp.items()}
    assert _decide(tool, inp, p, home) is None


@pytest.mark.parametrize("tool,inp,why", [
    ("Edit", {"file_path": "{p}/src/a.py"}, "Edit is blocked"),
    ("Write", {"file_path": "{p}/src/a.py"}, "Write is blocked"),
    ("MultiEdit", {}, "blocked"),
    ("NotebookEdit", {}, "blocked"),
    ("Bash", {"command": "ls"}, "Bash is blocked"),
    ("WebFetch", {"url": "https://x"}, "blocked"),
    ("WebSearch", {"query": "x"}, "blocked"),
    ("Agent", {"prompt": "x"}, "blocked"),
    ("Task", {"prompt": "x"}, "blocked"),
    ("mcp__hippocampus__remember", {}, "MCP tool"),
    ("mcp__hippocampus__send_message", {}, "MCP tool"),
    ("mcp__hippocampus__forget", {}, "MCP tool"),
    ("mcp__evil__recall", {}, "MCP tool"),  # same tool name, other server
    ("mcp__plugin_x_hippocampus__recall", {}, "MCP tool"),
    ("mcp__plugin_hippocampus_hippocampus__remember", {}, "MCP tool"),
    ("mcp__plugin_hippocampus_hippocampus__send_message", {}, "MCP tool"),
    ("mcp__plugin_hippocampus_other__recall", {}, "MCP tool"),
    ("Grep", {"pattern": "x"}, "single file"),  # default path = whole project
    ("Grep", {"pattern": "x", "path": "src"}, "single file"),
    ("Grep", {"pattern": "x", "path": "{p}"}, "single file"),
    ("Grep", {"pattern": "x", "path": "src/missing.py"}, "single file"),
    ("Grep", {"pattern": "x", "path": "{p}/.env"}, "secret path"),
    ("Grep", {"pattern": "x", "path": "{p}/innocent"}, "secret path"),
    ("Read", {"file_path": "/etc/hosts"}, "outside the project"),
    ("Read", {"file_path": "{p}/../../outside.txt"}, "outside the project"),
    ("Read", {"file_path": "src/../../../outside.txt"}, "outside the project"),
    ("Read", {"file_path": "{p}/link_out"}, "outside the project"),  # symlink escape
    ("Read", {"file_path": "{p}/innocent"}, "secret path"),  # symlink to .env
    ("Read", {"file_path": "{p}/.env"}, "secret path"),
    ("Read", {"file_path": "{p}/config/credentials.yml"}, "secret path"),
    ("Read", {"file_path": "{p}/server.PEM"}, "secret path"),
    ("Read", {"file_path": "{p}/tls.key"}, "secret path"),
    ("Read", {"file_path": "{p}/id_ed25519.pub"}, "secret path"),
    ("Read", {"file_path": "{p}/my_secret_notes.md"}, "secret path"),
    ("Read", {"file_path": "~/.ssh/id_rsa"}, "outside the project"),
    ("Grep", {"pattern": "x", "path": "/"}, "outside the project"),
    ("Grep", {"pattern": "x", "glob": "**/.env*"}, "secret path"),
    ("Grep", {"pattern": "x", "glob": "../*"}, "escapes"),
    ("Glob", {"pattern": "/etc/*"}, "escapes"),
    ("Glob", {"pattern": "../../**"}, "escapes"),
    ("Glob", {"pattern": "~/.aws/*"}, "escapes"),
    ("Glob", {"pattern": "**/*.pem"}, "secret path"),
    ("Glob", {"pattern": "*", "path": "{p}/.."}, "outside the project"),
])
def test_denied(proj, tool, inp, why):
    home, p = proj
    inp = {k: v.replace("{p}", str(p)) if isinstance(v, str) else v for k, v in inp.items()}
    reason = _decide(tool, inp, p, home)
    assert reason and why in reason
    assert reason.startswith("A2A peer-turn restriction") and "hippo a2a policy set codex@peer/x act" in reason


def test_relative_paths_resolve_against_real_cwd(proj, tmp_path):
    home, p = proj
    # cwd moved outside the project: a relative path must not be judged against the project dir
    assert "outside the project" in _decide("Read", {"file_path": "outside.txt"}, p, home, cwd=tmp_path)
    assert "outside the project" in _decide("Grep", {"pattern": "x", "path": "a.py"}, p, home, cwd=tmp_path)


def test_secret_home_dirs_even_inside_project(tmp_path):
    from hippocampus.a2a import guard

    home = tmp_path / "home"
    (home / ".config" / "gh").mkdir(parents=True)
    # a project that happens to contain a secret home dir
    assert "secret path" in guard.decide("Read", {"file_path": str(home / ".config/gh/hosts.yml")}, str(home / ".config"),
                                         str(home / ".config"), TURN, "answer", home)
    assert "/ or $HOME" in guard.decide("Read", {"file_path": str(home / "x")}, str(home), str(home), TURN, "answer", home)


def test_no_turn_or_act_policy_makes_no_decision(proj):
    home, p = proj
    assert _decide("Bash", {"command": "rm -rf /"}, p, home, turn=None) is None
    assert _decide("Bash", {"command": "ls"}, p, home, policy="act") is None


# --- session-key mapping ------------------------------------------------------

def test_hook_key_matches_mcp_key(hippo_env, monkeypatch):
    from hippocampus.a2a import guard
    from hippocampus.storage import sessions

    sid = "3f2a9c1e-77aa-4b1e-9d1f-0c0ffee00000"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
    assert sessions.derive_session_key(client="claude-code") == guard.session_key(sid) == sessions.claude_session_key(sid)
    for odd in ("a b/c", "x" * 200, "..weird.."):
        assert guard.session_key(odd) == sessions.claude_session_key(odd)

    # MCP side via the pid pointer the SessionStart/UserPromptSubmit hooks bind
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    monkeypatch.setenv("HIPPOCAMPUS_CLAUDE_PID", str(os.getppid()))
    monkeypatch.setenv("HIPPOCAMPUS_SESSION_KEY", f"cc-{sid}")
    sessions.bind_parent_pid()
    monkeypatch.delenv("HIPPOCAMPUS_SESSION_KEY")
    assert sessions.derive_session_key(client="claude-code") == guard.session_key(sid)


# --- entry points ---------------------------------------------------------------

@pytest.fixture
def a2a_db(hippo_env, monkeypatch, tmp_path):
    monkeypatch.setenv("HIPPOCAMPUS_A2A_ENABLED", "1")
    monkeypatch.delenv("HIPPOCAMPUS_A2A_DB", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    from hippocampus.a2a import state, store

    state.begin_peer_turn("cc-s1", {"msg_id": "m1", "from": "codex@peer", "thread_id": "t", "hop": 0})
    return store.db_path()


def _bash(cwd):
    return {"session_id": "s1", "cwd": str(cwd), "tool_name": "Bash", "tool_input": {"command": "ls"}}


def test_pretooluse_and_stop(a2a_db, tmp_path, monkeypatch):
    from hippocampus.a2a import guard

    assert "A2A peer-turn restriction" in guard.pretooluse(_bash(tmp_path), {})
    assert guard.pretooluse({**_bash(tmp_path), "session_id": "other"}, {}) is None  # other session unaffected
    assert "cannot resolve" in guard.pretooluse({**_bash(tmp_path), "session_id": ""}, {})

    from hippocampus.a2a import policy
    policy.set_for("codex@peer", "act")
    assert guard.pretooluse(_bash(tmp_path), {}) is None
    policy.set_for("codex@peer", "answer")

    guard.stop({"session_id": "s1"}, {})
    assert guard.pretooluse(_bash(tmp_path), {}) is None
    assert guard.pretooluse({**_bash(tmp_path), "session_id": ""}, {}) is None  # nothing active: no fail-closed


def test_peer_turn_ttl(a2a_db, tmp_path, monkeypatch):
    from hippocampus.a2a import guard, state

    monkeypatch.setenv("HIPPO_A2A_PEER_TURN_TTL_S", "0")
    assert guard.pretooluse(_bash(tmp_path), {}) is None
    assert state.active_peer_turn("cc-s1") is None


def test_flag_off_emits_nothing(a2a_db, tmp_path, monkeypatch):
    from hippocampus.a2a import guard

    monkeypatch.setenv("HIPPOCAMPUS_A2A_ENABLED", "0")
    assert guard.pretooluse(_bash(tmp_path), {}) is None
    assert guard.pretooluse({**_bash(tmp_path), "session_id": ""}, {}) is None


def _run_script(script, event, payload, env):
    return subprocess.run(["bash", str(script), event, "claude-code"], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=30)


def test_hook_script_end_to_end(a2a_db, tmp_path, monkeypatch):
    from hippocampus.clients import hooks

    monkeypatch.setenv("HIPPOCAMPUS_HIPPO_BIN", "/nonexistent/hippo")
    script = hooks._render_script("a2a-guard", "claude-code", tmp_path / "hooks" / "a2a-guard.sh")
    assert sys.executable in script.read_text()
    env = {**os.environ}
    env.pop("CLAUDE_PROJECT_DIR", None)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("x")

    r = _run_script(script, "pretooluse", _bash(proj), env)
    assert r.returncode == 2 and r.stdout == ""  # exit 2 blocks even under bypassPermissions
    assert "Bash is blocked" in r.stderr and "A2A peer-turn restriction" in r.stderr

    read = {"session_id": "s1", "cwd": str(proj), "tool_name": "Read", "tool_input": {"file_path": str(proj / "a.py")}}
    r = _run_script(script, "pretooluse", read, env)
    assert r.returncode == 0 and r.stdout == ""

    r = _run_script(script, "stop", {"session_id": "s1", "hook_event_name": "Stop"}, env)
    assert r.returncode == 0 and r.stdout == ""
    r = _run_script(script, "pretooluse", _bash(proj), env)
    assert r.returncode == 0 and r.stdout == ""

    # flag off / no a2a store: nothing at all
    r = _run_script(script, "pretooluse", _bash(proj), {**env, "HIPPOCAMPUS_A2A_ENABLED": "0"})
    assert r.returncode == 0 and r.stdout == ""
    r = _run_script(script, "pretooluse", _bash(proj), {**env, "HIPPOCAMPUS_A2A_DB": str(tmp_path / "none.db")})
    assert r.returncode == 0 and r.stdout == ""


def test_hook_script_fails_closed_when_guard_crashes(a2a_db, tmp_path):
    script = tmp_path / "g.sh"
    from hippocampus.clients import hooks

    body = hooks._repo_template("a2a-guard").read_text().replace("__HIPPO_PY__", "/usr/bin/false").replace("__HIPPO_BIN__", "/usr/bin/false")
    script.write_text(body)
    r = _run_script(script, "pretooluse", _bash(tmp_path), {**os.environ})
    assert r.returncode == 2 and "A2A guard failed" in r.stderr
    assert _run_script(script, "stop", {"session_id": "s1"}, {**os.environ}).returncode == 0  # Stop never blocks
