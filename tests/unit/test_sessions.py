"""Unit tests for session-key derivation.

The critical invariant for GUI editors (Cursor, VS Code): the MCP server and
the lifecycle hooks must derive the SAME session_key even though the editor
launches them from different, unstable cwds. They do that by keying off the
workspace root (WORKSPACE_FOLDER_PATHS) instead of cwd when there is no
controlling TTY. Terminal clients keep their tty+cwd key unchanged.
"""

from __future__ import annotations

import os

import pytest

_TERM_ENV = (
    "HIPPOCAMPUS_SESSION_KEY",
    "HIPPOCAMPUS_WORKSPACE",
    "WORKSPACE_FOLDER_PATHS",
    "TERM_SESSION_ID",
    "WEZTERM_PANE",
    "TMUX_PANE",
    "STY",
    "HIPPOCAMPUS_TTY",
    "HIPPOCAMPUS_CWD",
    "PWD",
)


@pytest.fixture
def clean_session_env(monkeypatch):
    """Drop every env var that influences derive_session_key."""
    for name in _TERM_ENV:
        monkeypatch.delenv(name, raising=False)
    yield monkeypatch


def _sessions():
    from hippocampus.storage import sessions

    return sessions


def test_workspace_key_is_stable_across_cwd_without_tty(clean_session_env):
    """The Cursor bug: MCP (cwd=/) and hooks (cwd=~/.cursor) must agree.

    With WORKSPACE_FOLDER_PATHS set and no TTY, the key is derived from the
    workspace root, so it is identical regardless of the launching cwd.
    """
    sessions = _sessions()
    clean_session_env.setattr(sessions, "_detect_tty", lambda: None)
    clean_session_env.setenv("WORKSPACE_FOLDER_PATHS", "/home/dev/work/hippocampus")

    # MCP server is launched by Cursor with cwd=/
    clean_session_env.setenv("HIPPOCAMPUS_CWD", "/")
    mcp_key = sessions.derive_session_key()

    # Hooks are launched by Cursor from a different cwd (~/.cursor).
    clean_session_env.setenv("HIPPOCAMPUS_CWD", "/home/dev/.cursor")
    hook_key = sessions.derive_session_key()

    assert mcp_key == hook_key
    assert mcp_key.startswith("ws-")
    assert "hippocampus" in mcp_key


def test_workspace_distinguishes_projects(clean_session_env):
    """Different workspaces must still get different keys."""
    sessions = _sessions()
    clean_session_env.setattr(sessions, "_detect_tty", lambda: None)

    clean_session_env.setenv("WORKSPACE_FOLDER_PATHS", "/home/dev/work/hippocampus")
    a = sessions.derive_session_key()
    clean_session_env.setenv("WORKSPACE_FOLDER_PATHS", "/home/dev/work/acme-orders")
    b = sessions.derive_session_key()

    assert a != b


def test_terminal_key_ignores_workspace(clean_session_env):
    """Terminal clients (TTY present) keep their existing tty+cwd key.

    Even if WORKSPACE_FOLDER_PATHS leaks into the environment, a client with a
    real TTY must not switch to a workspace key — that would needlessly churn
    already-working terminal sessions.
    """
    sessions = _sessions()
    clean_session_env.setattr(sessions, "_detect_tty", lambda: "/dev/ttys011")
    clean_session_env.setenv("WORKSPACE_FOLDER_PATHS", "/home/dev/work/hippocampus")
    clean_session_env.setenv("HIPPOCAMPUS_CWD", "/home/dev/work/foo")

    key = sessions.derive_session_key()

    assert key.startswith("tty-")
    assert "ws-" not in key
    assert "cwd-foo" in key


def test_term_hint_also_blocks_workspace(clean_session_env):
    """A terminal-session hint (iTerm/tmux) counts as a terminal context too."""
    sessions = _sessions()
    clean_session_env.setattr(sessions, "_detect_tty", lambda: None)
    clean_session_env.setenv("TERM_SESSION_ID", "w0t7p0:ABC")
    clean_session_env.setenv("WORKSPACE_FOLDER_PATHS", "/home/dev/work/hippocampus")
    clean_session_env.setenv("HIPPOCAMPUS_CWD", "/home/dev/work/foo")

    key = sessions.derive_session_key()

    assert key.startswith("term-")
    assert "ws-" not in key


def test_cwd_fallback_when_no_workspace(clean_session_env):
    """Without a TTY and without a workspace, fall back to cwd (unchanged)."""
    sessions = _sessions()
    clean_session_env.setattr(sessions, "_detect_tty", lambda: None)
    clean_session_env.setenv("HIPPOCAMPUS_CWD", "/home/dev/work/hippocampus")

    key = sessions.derive_session_key()

    assert key.startswith("cwd-")
    assert "hippocampus" in key


def test_explicit_session_key_wins(clean_session_env):
    """An explicit HIPPOCAMPUS_SESSION_KEY overrides every heuristic."""
    sessions = _sessions()
    clean_session_env.setattr(sessions, "_detect_tty", lambda: "/dev/ttys011")
    clean_session_env.setenv("WORKSPACE_FOLDER_PATHS", "/home/dev/work/hippocampus")
    clean_session_env.setenv("HIPPOCAMPUS_SESSION_KEY", "my-explicit-key")

    assert sessions.derive_session_key() == "my-explicit-key"


def test_multi_root_workspace_uses_first(clean_session_env):
    """Multi-root workspaces key off the first root (matches MCP + hook)."""
    sessions = _sessions()
    clean_session_env.setattr(sessions, "_detect_tty", lambda: None)

    first_only = "/home/dev/work/hippocampus"
    multi = first_only + os.pathsep + "/home/dev/work/acme-orders"

    clean_session_env.setenv("WORKSPACE_FOLDER_PATHS", first_only)
    key_single = sessions.derive_session_key()
    clean_session_env.setenv("WORKSPACE_FOLDER_PATHS", multi)
    key_multi = sessions.derive_session_key()

    assert key_single == key_multi
