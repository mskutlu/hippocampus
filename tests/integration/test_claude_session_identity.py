"""Claude Code session identity: keyed on Claude's session_id, never tty/cwd."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

CLAUDE = "claude-code"


@pytest.fixture
def claude_env(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", CLAUDE)
    monkeypatch.setenv("HIPPOCAMPUS_TTY", "/dev/ttys001")
    return monkeypatch


def _as_hook(monkeypatch, claude_sid: str, *, cwd: str, tty: str, pid: int = 4242) -> None:
    monkeypatch.setenv("HIPPOCAMPUS_SESSION_KEY", f"cc-{claude_sid}")
    monkeypatch.setenv("HIPPOCAMPUS_CLAUDE_PID", str(pid))
    monkeypatch.setenv("HIPPOCAMPUS_CWD", cwd)
    monkeypatch.setenv("HIPPOCAMPUS_TTY", tty)


def _as_mcp(monkeypatch, *, parent_pid: int = 4242, env_sid: str | None = None) -> None:
    from hippocampus.storage import sessions

    for name in ("HIPPOCAMPUS_SESSION_KEY", "HIPPOCAMPUS_CLAUDE_PID"):
        monkeypatch.delenv(name, raising=False)
    if env_sid:
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", env_sid)
    monkeypatch.setenv("HIPPOCAMPUS_CWD", "/somewhere/else")
    monkeypatch.setenv("HIPPOCAMPUS_TTY", "")
    monkeypatch.setattr(sessions.os, "getppid", lambda: parent_pid)


def test_cwd_change_keeps_same_session(claude_env):
    from hippocampus.storage import sessions

    _as_hook(claude_env, "aaa-111", cwd="/work/hippocampus", tty="/dev/ttys001")
    first = sessions.ensure_session(CLAUDE)
    _as_hook(claude_env, "aaa-111", cwd="/work/harness2", tty="/dev/ttys009")
    assert sessions.ensure_session(CLAUDE) == first


def test_two_claude_sessions_do_not_cross_talk(claude_env):
    from hippocampus.mcp import tools

    _as_hook(claude_env, "aaa-111", cwd="/work/a", tty="/dev/ttys001", pid=100)
    a = tools.log_progress(kind="goal", content="Goal of A")["entry"]["session_id"]
    _as_hook(claude_env, "bbb-222", cwd="/work/a", tty="/dev/ttys001", pid=200)
    b = tools.log_progress(kind="goal", content="Goal of B")["entry"]["session_id"]
    assert a != b

    _as_hook(claude_env, "aaa-111", cwd="/work/a", tty="/dev/ttys001", pid=100)
    assert tools.get_progress()["goal"] == "Goal of A"
    _as_hook(claude_env, "bbb-222", cwd="/work/a", tty="/dev/ttys001", pid=200)
    assert tools.get_progress()["goal"] == "Goal of B"


def test_mcp_resolves_to_hook_session_via_parent_pid(claude_env):
    from hippocampus.mcp import tools
    from hippocampus.storage import sessions

    _as_hook(claude_env, "after-clear", cwd="/work/a", tty="/dev/ttys001")
    sessions.bind_parent_pid()
    hook_sid = sessions.ensure_session(CLAUDE)

    _as_mcp(claude_env, env_sid="stale-before-clear")
    entry = tools.log_progress(kind="done", content="from MCP")["entry"]
    assert entry["session_id"] == hook_sid


def test_mcp_falls_back_to_claude_env_session_id(claude_env):
    from hippocampus.mcp import tools
    from hippocampus.storage import sessions

    _as_hook(claude_env, "env-id", cwd="/work/a", tty="/dev/ttys001")
    hook_sid = sessions.ensure_session(CLAUDE)

    _as_mcp(claude_env, parent_pid=9999, env_sid="env-id")
    assert tools.log_progress(kind="done", content="x")["entry"]["session_id"] == hook_sid


def test_end_progress_rotates_within_same_claude_session(claude_env):
    from hippocampus.mcp import tools

    _as_hook(claude_env, "aaa-111", cwd="/work/a", tty="/dev/ttys001")
    tools.log_progress(kind="goal", content="Ship it")
    out = tools.end_progress()
    assert out["rotated"] is True
    assert tools.get_progress()["session_id"] == out["new_session_id"]

    handoff = tools.get_handoff()
    assert handoff["resumed_from_previous_session"] is True
    assert handoff["session_id"] == out["previous_session_id"]
    assert handoff["goal"] == "Ship it"


def test_claude_code_rules_file_gets_no_working_block(claude_env, tmp_path):
    from hippocampus import config
    from hippocampus.clients import registry
    from hippocampus.mcp import tools

    rules = registry.by_name(CLAUDE).rules_path
    rules.parent.mkdir(parents=True, exist_ok=True)
    stale = f"{config.WORKING_MARKER_START}\nold goal\n{config.WORKING_MARKER_END}\n"
    rules.write_text("# Mine\n\n" + stale, encoding="utf-8")

    _as_hook(claude_env, "aaa-111", cwd="/work/a", tty="/dev/ttys001")
    tools.log_progress(kind="goal", content="Session-specific goal")

    text = rules.read_text(encoding="utf-8")
    assert config.WORKING_MARKER_START not in text
    assert "Session-specific goal" not in text
    assert "# Mine" in text


def test_other_clients_ignore_claude_session_id(hippo_env, monkeypatch):
    from hippocampus.storage import sessions

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "aaa-111")
    monkeypatch.setenv("HIPPOCAMPUS_TTY", "/dev/ttys001")
    monkeypatch.setenv("HIPPOCAMPUS_CWD", "/work/a")
    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", CLAUDE)
    assert not sessions.derive_session_key(client="devin").startswith("cc-")
    assert sessions.derive_session_key(client=CLAUDE) == "cc-aaa-111"


def test_inject_strips_claude_working_block_keeps_long_block(hippo_env):
    from click.testing import CliRunner

    from hippocampus import config
    from hippocampus.cli.main import cli
    from hippocampus.clients import registry

    rules = registry.by_name(CLAUDE).rules_path
    rules.parent.mkdir(parents=True, exist_ok=True)
    rules.write_text(f"# Mine\n\n{config.WORKING_MARKER_START}\nstale\n{config.WORKING_MARKER_END}\n", encoding="utf-8")

    out = CliRunner().invoke(cli, ["inject", "--commit", "--only", CLAUDE, "--no-sync"])
    assert out.exit_code == 0, out.output
    assert "working=removed" in out.output
    text = rules.read_text(encoding="utf-8")
    assert config.WORKING_MARKER_START not in text
    assert config.INJECTION_MARKER_START in text


def _hippo_bin() -> Path:
    candidate = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "hippo"
    if not candidate.exists():
        pytest.skip("hippo binary not available in repo .venv")
    return candidate


def _run_hook(script: Path, payload: dict, *, cwd: Path, tty: str) -> None:
    env = dict(os.environ, HIPPOCAMPUS_TTY=tty, PWD=str(cwd))
    env.pop("HIPPOCAMPUS_CWD", None)
    proc = subprocess.run(
        ["bash", str(script), CLAUDE],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr


def _open_sessions() -> list[tuple[str, str]]:
    from hippocampus.storage.db import get_ro_conn

    with get_ro_conn() as conn:
        rows = conn.execute(
            "SELECT id, session_key FROM sessions WHERE client = ? AND ended_at IS NULL",
            (CLAUDE,),
        ).fetchall()
    return [(r["id"], r["session_key"]) for r in rows]


def _wait_for_session(key: str) -> str | None:
    for _ in range(100):
        found = [sid for sid, k in _open_sessions() if k == key]
        if found:
            return found[0]
        time.sleep(0.1)
    return None


def _wait_for_ask(session_id: str, content: str) -> bool:
    from hippocampus.storage import ledger

    for _ in range(100):
        if any(e.kind == "ask" and e.content == content for e in ledger.current_entries(session_id)):
            return True
        time.sleep(0.1)
    return False


def test_rendered_hooks_key_on_claude_session_id(claude_env, tmp_path):
    from hippocampus.clients import hooks
    from hippocampus.mcp import tools

    claude_env.setenv("HIPPOCAMPUS_HIPPO_BIN", str(_hippo_bin()))
    start = hooks._render_script("session-start", CLAUDE, tmp_path / "hooks" / "start.sh")
    submit = hooks._render_script("user-prompt-submit", CLAUDE, tmp_path / "hooks" / "submit.sh")
    repo_a, repo_b = tmp_path / "hippocampus", tmp_path / "harness2"
    repo_a.mkdir()
    repo_b.mkdir()

    _run_hook(start, {"session_id": "conv-1"}, cwd=repo_a, tty="/dev/ttys001")
    _run_hook(submit, {"session_id": "conv-1", "prompt": "cd into harness2"}, cwd=repo_b, tty="/dev/ttys004")
    assert [key for _, key in _open_sessions()] == ["cc-conv-1"]
    conv1 = _open_sessions()[0][0]
    assert _wait_for_ask(conv1, "cd into harness2")

    _as_mcp(claude_env, parent_pid=os.getpid(), env_sid="conv-1")
    assert tools.log_progress(kind="done", content="mcp write")["entry"]["session_id"] == conv1

    _run_hook(submit, {"session_id": "conv-2", "prompt": "after clear"}, cwd=repo_b, tty="/dev/ttys004")
    conv2 = _wait_for_session("cc-conv-2")
    assert conv2 and conv2 != conv1
    assert _wait_for_ask(conv2, "after clear")
    assert tools.log_progress(kind="done", content="mcp after clear")["entry"]["session_id"] == conv2
