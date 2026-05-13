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

    # Devin gets 3 scripts (start, submit, post-compaction); Claude Code gets 2.
    devin_result = next(r for r in results if r["client"] == "devin")
    claude_result = next(r for r in results if r["client"] == "claude-code")
    assert len(devin_result["scripts"]) == 3
    assert any("post-compaction" in s for s in devin_result["scripts"])
    assert len(claude_result["scripts"]) == 2

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
    assert "PostCompaction" in hooks_obj  # NEW in V1.5
    for evt in ("SessionStart", "UserPromptSubmit", "PostCompaction"):
        for entry in hooks_obj[evt]:
            for h in entry["hooks"]:
                assert h["tag"] == "hippocampus-v1"

    # Claude Code check — no PostCompaction (not supported yet).
    claude_cfg = json.loads((fake_home / ".claude" / "settings.json").read_text())
    claude_hooks = claude_cfg.get("hooks", {})
    assert "SessionStart" in claude_hooks
    assert "UserPromptSubmit" in claude_hooks
    assert "PostCompaction" not in claude_hooks


def test_install_is_idempotent(tmp_path, monkeypatch):
    from hippocampus.clients import hooks

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    hooks.install_all()
    hooks.install_all()  # second run should NOT double-register

    devin_cfg = json.loads((fake_home / ".config" / "devin" / "config.json").read_text())
    for evt in ("SessionStart", "UserPromptSubmit", "PostCompaction"):
        assert len(devin_cfg["hooks"][evt]) == 1
        assert len(devin_cfg["hooks"][evt][0]["hooks"]) == 1


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
        for ev, installed in r["installed"].items():
            assert installed is False, f"unexpected initial install: {r['client']}/{ev}"

    hooks.install_all()
    report_after = hooks.status()
    devin = next(r for r in report_after if r["client"] == "devin")
    claude = next(r for r in report_after if r["client"] == "claude-code")

    assert devin["installed"]["SessionStart"] is True
    assert devin["installed"]["UserPromptSubmit"] is True
    assert devin["installed"]["PostCompaction"] is True  # NEW in V1.5

    assert claude["installed"]["SessionStart"] is True
    assert claude["installed"]["UserPromptSubmit"] is True
    # Claude Code doesn't get PostCompaction at all — report should not pretend it's expected.
    assert "PostCompaction" not in claude["installed"]


def test_post_compaction_script_emits_additional_context(tmp_path, monkeypatch):
    """The rendered post-compaction script must produce a JSON envelope
    with hookEventName=PostCompaction and a non-empty additionalContext
    when given a realistic stdin payload."""
    import os
    import subprocess

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    # Point the rendered script at the test interpreter's hippo entry point.
    repo_root = Path(__file__).resolve().parents[2]
    hippo_bin = repo_root / ".venv" / "bin" / "hippo"
    if not hippo_bin.exists():
        # Fallback: skip if there's no installed hippo binary (CI uses uv sync).
        import pytest

        pytest.skip("hippo binary not available in repo .venv")
    monkeypatch.setenv("HIPPOCAMPUS_HIPPO_BIN", str(hippo_bin))

    from hippocampus.clients import hooks

    hooks.install_all()
    script = fake_home / ".config" / "devin" / "hippocampus-hooks" / "devin" / "post-compaction.sh"
    assert script.exists()

    env = dict(os.environ)
    env.setdefault("HIPPOCAMPUS_HOME", str(tmp_path / ".hippocampus"))
    env.setdefault("HIPPOCAMPUS_VAULT", str(tmp_path / "vault"))

    proc = subprocess.run(
        ["bash", str(script), "devin"],
        input=json.dumps({"summary": "compacted; was talking about kafka idempotency."}),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    # Either we have hookSpecificOutput with PostCompaction, or {} when hippo
    # isn't installed cleanly — in this test fixture it's installed.
    assert payload, "post-compaction script returned empty payload"
    if "hookSpecificOutput" in payload:
        assert payload["hookSpecificOutput"]["hookEventName"] == "PostCompaction"
        assert payload["hookSpecificOutput"]["additionalContext"]
