"""V9 W1-4 — auto-pin on access threshold.

When a fragment has been accessed `auto_pin_access_threshold` times, the next
boost flips its `pinned` flag automatically. This is zombie protection: heavy
use can no longer be drowned by decay.
"""

from __future__ import annotations


def test_auto_pin_fires_at_threshold(hippo_env, monkeypatch):
    from hippocampus.dynamics import boost as boost_dyn
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    monkeypatch.setenv("HIPPO_AUTO_PIN_ACCESS_THRESHOLD", "3")
    res = T.remember(content="Repeat-accessed knowledge.", summary="r")
    fid = res["fragment"]["id"]
    assert F.get(fid).pinned is False

    # Threshold = 3, so the 3rd boost must pin
    for _ in range(2):
        boost_dyn.boost(fid, client="pytest")
        assert F.get(fid).pinned is False
    boost_dyn.boost(fid, client="pytest")
    assert F.get(fid).pinned is True


def test_auto_pin_disabled_at_zero(hippo_env, monkeypatch):
    from hippocampus.dynamics import boost as boost_dyn
    from hippocampus.mcp import tools as T
    from hippocampus.storage import fragments as F

    monkeypatch.setenv("HIPPO_AUTO_PIN_ACCESS_THRESHOLD", "0")
    res = T.remember(content="Never-pinned by auto.", summary="np")
    fid = res["fragment"]["id"]

    for _ in range(20):
        boost_dyn.boost(fid, client="pytest")
    assert F.get(fid).pinned is False


def test_auto_pin_records_feedback_event(hippo_env, monkeypatch):
    from hippocampus.dynamics import boost as boost_dyn
    from hippocampus.mcp import tools as T
    from hippocampus.storage.db import get_ro_conn

    monkeypatch.setenv("HIPPO_AUTO_PIN_ACCESS_THRESHOLD", "2")
    res = T.remember(content="x", summary="x")
    fid = res["fragment"]["id"]
    boost_dyn.boost(fid, client="pytest")
    boost_dyn.boost(fid, client="pytest")

    with get_ro_conn() as conn:
        rows = conn.execute(
            "SELECT kind, reason FROM feedback_log WHERE fragment_id = ? AND kind='auto-pin'",
            (fid,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["reason"] == "access-threshold"
