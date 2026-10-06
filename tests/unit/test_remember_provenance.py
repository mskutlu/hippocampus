"""remember() defaults source_ref to the active session; recall exposes it."""

from __future__ import annotations


def _session_count() -> int:
    from hippocampus.storage.db import get_ro_conn

    with get_ro_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


def test_remember_defaults_source_ref_to_active_session(hippo_env):
    from hippocampus.mcp import tools
    from hippocampus.storage import sessions

    sid = sessions.open_session("pytest", session_key=sessions.derive_session_key())

    assert tools.remember("provenance defaults to session")["fragment"]["source_ref"] == sid


def test_remember_without_session_leaves_source_ref_and_opens_none(hippo_env):
    from hippocampus.mcp import tools

    out = tools.remember("no session is active here")

    assert out["fragment"]["source_ref"] is None
    assert _session_count() == 0


def test_remember_keeps_explicit_source_ref(hippo_env):
    from hippocampus.mcp import tools
    from hippocampus.storage import sessions

    sessions.open_session("pytest", session_key=sessions.derive_session_key())

    out = tools.remember("explicit ref wins", source_ref="doc:readme")

    assert out["fragment"]["source_ref"] == "doc:readme"


def test_remember_global_scope_skips_session_default(hippo_env):
    from hippocampus.mcp import tools
    from hippocampus.storage import sessions

    sessions.open_session("pytest", session_key=sessions.derive_session_key())

    out = tools.remember("deliberately global fact", scope="global")

    assert out["fragment"]["source_ref"] is None


def test_recall_includes_source_ref(hippo_env):
    from hippocampus.mcp import tools

    tools.remember("zebrafish provenance marker", source_ref="doc:readme")

    hits = tools.recall("zebrafish", boost=False)["fragments"]

    assert [h["source_ref"] for h in hits] == ["doc:readme"]
