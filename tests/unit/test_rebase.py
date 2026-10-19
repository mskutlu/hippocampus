"""Reversible confidence rebase."""

from __future__ import annotations

import pytest


@pytest.fixture
def seeded(hippo_env):
    from hippocampus.storage import feedback, fragments as F

    def make(name, conf, pinned=False):
        f = F.create(name, pinned=pinned)
        F.update_fields(f.id, confidence=conf)
        return f.id

    ids = {
        "high": make("high", 0.9),
        "low": make("low", 0.3),
        "pinned_by_user": make("user pin", 0.9, pinned=True),
        "useful": make("useful", 0.9),
        "auto": make("auto pinned", 0.9, pinned=True),
        "manual_repin": make("repinned", 0.9, pinned=True),
        "unknown_pin": make("unknown pin", 0.9, pinned=True),
        "neg": make("negated", 0.9),
    }
    feedback.log(ids["pinned_by_user"], "pin")
    feedback.log(ids["useful"], "useful")
    feedback.log(ids["auto"], "auto-pin", reason="access-threshold")
    feedback.log(ids["manual_repin"], "auto-pin")
    feedback.log(ids["manual_repin"], "pin")
    feedback.log(ids["neg"], "negative", delta=-0.04, reason="user_negation_inferred")
    feedback.log(ids["neg"], "negative", delta=-0.02, reason="wrong fact")
    return ids


def _state():
    from hippocampus.storage import fragments as F

    return {f.id: (round(f.confidence, 6), f.pinned) for f in F.list_all(limit=100)}


def test_dry_run_writes_nothing(seeded):
    from hippocampus.dynamics import rebase

    before = _state()
    out = rebase.dry_run()
    assert out["confidence_changed"] > 0 and out["unpinned"] == 1
    assert _state() == before


def test_apply_rules(seeded):
    from hippocampus.dynamics import rebase
    from hippocampus.storage import fragments as F

    rebase.apply()
    got = {k: F.get(v) for k, v in seeded.items()}
    assert got["high"].confidence == pytest.approx(0.7)
    assert got["low"].confidence == pytest.approx(0.3)
    assert got["pinned_by_user"].confidence == pytest.approx(0.9)
    assert got["useful"].confidence == pytest.approx(0.9)
    assert got["neg"].confidence == pytest.approx(0.7 - 0.04 - 0.20)
    assert got["auto"].pinned is False
    assert got["manual_repin"].pinned is True and got["unknown_pin"].pinned is True
    assert all(f.evidence_at for k, f in got.items() if k in ("high", "neg"))


def test_restore_round_trip(seeded):
    from hippocampus.dynamics import rebase

    before = _state()
    rebase.apply()
    assert _state() != before
    assert rebase.restore()["restored"] == len(before)
    assert _state() == before
    rebase.apply()


def test_second_apply_refused(seeded):
    from hippocampus.dynamics import rebase

    rebase.apply()
    with pytest.raises(RuntimeError):
        rebase.apply()


def test_unpin_is_logged_and_bumps_updated_at(seeded):
    from hippocampus.dynamics import rebase
    from hippocampus.storage import fragments as F
    from hippocampus.storage.db import get_ro_conn

    old = F.get(seeded["auto"]).updated_at
    rebase.apply()
    assert F.get(seeded["auto"]).updated_at > old
    with get_ro_conn() as conn:
        row = conn.execute(
            "SELECT reason FROM feedback_log WHERE fragment_id = ? AND kind = 'unpin'", (seeded["auto"],)
        ).fetchone()
    assert row["reason"] == "evidence-rebase"


def test_cli_requires_a_mode(seeded):
    from click.testing import CliRunner

    from hippocampus.cli.main import cli

    runner = CliRunner()
    assert runner.invoke(cli, ["confidence", "rebase"]).exit_code != 0
    blocked = runner.invoke(cli, ["confidence", "rebase", "--apply"])
    assert blocked.exit_code != 0 and "evidence" in blocked.output
    forced = runner.invoke(cli, ["confidence", "rebase", "--apply", "--force"])
    assert forced.exit_code == 0
    runner.invoke(cli, ["confidence", "rebase", "--restore"])
    out = runner.invoke(cli, ["confidence", "rebase", "--dry-run"])
    assert out.exit_code == 0 and "confidence_changed" in out.output
