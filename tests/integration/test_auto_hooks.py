"""Tests for V1.4 auto-trigger hooks installer."""

from __future__ import annotations

import json
from pathlib import Path


def test_install_creates_scripts_and_registers_hooks(tmp_path, monkeypatch):
    """`install_all` must drop executable scripts and add entries to both configs."""
    from hippocampus.clients import hooks

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Pretend to find a hippo binary on PATH.
    monkeypatch.setenv("HIPPOCAMPUS_HIPPO_BIN", "/opt/fake/bin/hippo")
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    results = hooks.install_all()
    assert len(results) == 2

    for r in results:
        for script in r["scripts"]:
            p = Path(script)
            assert p.exists()
            assert p.stat().st_mode & 0o111  # executable
            body = p.read_text()
            assert "/opt/fake/bin/hippo" in body

    # Devin config check
    devin_cfg = json.loads((fake_home / ".config" / "devin" / "config.json").read_text())
    hooks_obj = devin_cfg.get("hooks", {})
    assert "SessionStart" in hooks_obj
    assert "UserPromptSubmit" in hooks_obj
    # Every registered hook must be tagged
    for evt in ("SessionStart", "UserPromptSubmit"):
        for entry in hooks_obj[evt]:
            for h in entry["hooks"]:
                assert h["tag"] == "hippocampus-v1"

    # Claude Code check
    claude_cfg = json.loads((fake_home / ".claude" / "settings.json").read_text())
    assert "SessionStart" in claude_cfg.get("hooks", {})


def test_install_is_idempotent(tmp_path, monkeypatch):
    from hippocampus.clients import hooks

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    hooks.install_all()
    hooks.install_all()  # second run should NOT double-register

    devin_cfg = json.loads((fake_home / ".config" / "devin" / "config.json").read_text())
    assert len(devin_cfg["hooks"]["SessionStart"]) == 1
    assert len(devin_cfg["hooks"]["SessionStart"][0]["hooks"]) == 1


def test_uninstall_removes_only_hippocampus(tmp_path, monkeypatch):
    from hippocampus.clients import hooks

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    # Seed the Devin config with an unrelated hook that should survive.
    other_hook = {
        "SessionStart": [
            {"matcher": "", "hooks": [{"type": "command", "command": "echo hi", "tag": "not-us"}]}
        ]
    }
    cfg_path = fake_home / ".config" / "devin" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"hooks": other_hook}))

    hooks.install_all()
    hooks.uninstall_all()

    remaining = json.loads(cfg_path.read_text()).get("hooks", {})
    # Unrelated hook is preserved
    assert remaining["SessionStart"][0]["hooks"][0]["command"] == "echo hi"
    # Hippocampus hook is gone
    all_cmds = [h["command"] for e in remaining.get("SessionStart", []) for h in e["hooks"]]
    assert not any("hippocampus" in c for c in all_cmds)


def test_status_reports_per_client(tmp_path, monkeypatch):
    from hippocampus.clients import hooks

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    report = hooks.status()
    assert len(report) == 2
    for r in report:
        assert r["installed"]["SessionStart"] is False
        assert r["installed"]["UserPromptSubmit"] is False

    hooks.install_all()
    report_after = hooks.status()
    for r in report_after:
        assert r["installed"]["SessionStart"] is True
        assert r["installed"]["UserPromptSubmit"] is True
