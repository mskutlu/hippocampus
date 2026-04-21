"""Install / uninstall Hippocampus lifecycle hooks into Devin + Claude Code.

Each supported client exposes a JSON hooks config that Devin-for-Terminal
(and Claude Code) read on start-up.  We render per-client copies of the
shell scripts (with the absolute `hippo` binary baked in) and register
them as `SessionStart` and `UserPromptSubmit` hooks.

Uninstall is surgical — we only remove entries whose `command` points into
our hooks directory; hooks belonging to other tools are left alone.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hippocampus import config


HOOKS_DIRNAME = "hippocampus-hooks"


def _hippo_bin() -> str:
    env = os.environ.get("HIPPOCAMPUS_HIPPO_BIN")
    if env:
        return env
    found = shutil.which("hippo")
    if found:
        return found
    # Fallback: absolute path under the repo checkout's venv
    repo = Path(__file__).resolve().parents[3]
    candidate = repo / ".venv" / "bin" / "hippo"
    if candidate.exists():
        return str(candidate)
    return "hippo"


def _repo_template(name: str) -> Path:
    return Path(__file__).resolve().parents[3] / "scripts" / "hooks" / f"{name}.sh.template"


def _install_dir_for(client: str) -> Path:
    """Where to drop the per-client hook scripts.

    We use Devin's own config dir even for the claude-code hooks so we have
    exactly one source-of-truth location for the script files.
    """
    home = Path.home()
    return home / ".config" / "devin" / HOOKS_DIRNAME / client


def _render_script(template_name: str, client: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = _repo_template(template_name)
    body = src.read_text(encoding="utf-8").replace("__HIPPO_BIN__", _hippo_bin())
    dest.write_text(body, encoding="utf-8")
    st = dest.stat().st_mode
    dest.chmod(st | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dest


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak = path.with_suffix(path.suffix + f".{stamp}.pre-hippo-hooks.bak")
    shutil.copy2(path, bak)
    return bak


HOOK_TAG = "hippocampus-v1"  # used to tag our hook entries for clean removal


def _build_claude_format_entries(client: str, start_path: Path, submit_path: Path) -> dict[str, Any]:
    """Return a dict shaped like the Claude-Code hooks schema for the two events."""
    return {
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"bash {start_path} {client}",
                        "timeout": 5,
                        "tag": HOOK_TAG,
                    }
                ],
            }
        ],
        "UserPromptSubmit": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"bash {submit_path} {client}",
                        "timeout": 5,
                        "tag": HOOK_TAG,
                    }
                ],
            }
        ],
    }


def _merge_hooks(existing: dict, new_entries: dict) -> dict:
    """Merge our entries into an existing hooks dict without touching others."""
    merged = dict(existing or {})
    for event, entries in new_entries.items():
        bucket = list(merged.get(event, []))
        # Drop any prior Hippocampus entries (by tag) so install is idempotent.
        bucket = [e for e in bucket if not _entry_is_ours(e)]
        bucket.extend(entries)
        merged[event] = bucket
    return merged


def _entry_is_ours(entry: dict) -> bool:
    for h in (entry.get("hooks") or []):
        if h.get("tag") == HOOK_TAG:
            return True
        cmd = h.get("command", "")
        if HOOKS_DIRNAME in cmd:
            return True
    return False


def _strip_hooks(existing: dict) -> dict:
    cleaned: dict = {}
    for event, entries in (existing or {}).items():
        kept = []
        for entry in entries:
            hs = [h for h in (entry.get("hooks") or []) if not (h.get("tag") == HOOK_TAG or HOOKS_DIRNAME in h.get("command", ""))]
            if hs:
                kept.append({**entry, "hooks": hs})
        if kept:
            cleaned[event] = kept
    return cleaned


def install_for_devin() -> dict[str, Any]:
    """Install hooks into ~/.config/devin/config.json."""
    client = "devin"
    hooks_dir = _install_dir_for(client)
    start_path = _render_script("session-start", client, hooks_dir / "session-start.sh")
    submit_path = _render_script("user-prompt-submit", client, hooks_dir / "user-prompt-submit.sh")

    cfg_path = Path.home() / ".config" / "devin" / "config.json"
    _backup(cfg_path)
    cfg = _load_json(cfg_path)
    cfg["hooks"] = _merge_hooks(cfg.get("hooks", {}), _build_claude_format_entries(client, start_path, submit_path))
    _write_json(cfg_path, cfg)
    return {"client": client, "config": str(cfg_path), "scripts": [str(start_path), str(submit_path)]}


def install_for_claude_code() -> dict[str, Any]:
    """Install hooks into ~/.claude/settings.json (preferred) or ~/.claude.json."""
    client = "claude-code"
    hooks_dir = _install_dir_for(client)
    start_path = _render_script("session-start", client, hooks_dir / "session-start.sh")
    submit_path = _render_script("user-prompt-submit", client, hooks_dir / "user-prompt-submit.sh")

    # Prefer ~/.claude/settings.json (Claude Code's canonical hooks file).
    cfg_path = Path.home() / ".claude" / "settings.json"
    _backup(cfg_path)
    cfg = _load_json(cfg_path)
    cfg["hooks"] = _merge_hooks(cfg.get("hooks", {}), _build_claude_format_entries(client, start_path, submit_path))
    _write_json(cfg_path, cfg)
    return {"client": client, "config": str(cfg_path), "scripts": [str(start_path), str(submit_path)]}


def install_all() -> list[dict[str, Any]]:
    return [install_for_devin(), install_for_claude_code()]


def uninstall_all() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for cfg_path in (
        Path.home() / ".config" / "devin" / "config.json",
        Path.home() / ".claude" / "settings.json",
    ):
        if not cfg_path.exists():
            results.append({"config": str(cfg_path), "status": "missing"})
            continue
        _backup(cfg_path)
        cfg = _load_json(cfg_path)
        cfg["hooks"] = _strip_hooks(cfg.get("hooks", {}))
        _write_json(cfg_path, cfg)
        results.append({"config": str(cfg_path), "status": "stripped"})
    return results


def status() -> list[dict[str, Any]]:
    """Return a report per client of whether hooks are installed."""
    reports: list[dict[str, Any]] = []
    for client, cfg_path in (
        ("devin", Path.home() / ".config" / "devin" / "config.json"),
        ("claude-code", Path.home() / ".claude" / "settings.json"),
    ):
        data = _load_json(cfg_path) if cfg_path.exists() else {}
        events = (data.get("hooks") or {})
        installed = {
            ev: any(_entry_is_ours(e) for e in events.get(ev, []))
            for ev in ("SessionStart", "UserPromptSubmit")
        }
        reports.append({"client": client, "config": str(cfg_path), "installed": installed})
    return reports
