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
    packaged = Path(__file__).resolve().parents[1] / "assets" / "hooks" / f"{name}.sh.template"
    if packaged.exists():
        return packaged
    return Path(__file__).resolve().parents[3] / "scripts" / "hooks" / f"{name}.sh.template"


def _install_dir_for(client: str) -> Path:
    """Where to drop the per-client hook scripts.

    We use Devin's own config dir even for the claude-code hooks so we have
    exactly one source-of-truth location for the script files.
    """
    home = Path.home()
    if client == "antigravity":
        return home / ".gemini" / "antigravity" / "hooks" / HOOKS_DIRNAME
    if client == "cursor":
        return home / ".cursor" / "hooks" / HOOKS_DIRNAME
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


def _build_claude_format_entries(
    client: str,
    start_path: Path,
    submit_path: Path,
    *,
    post_compaction_path: Path | None = None,
) -> dict[str, Any]:
    """Return a dict shaped like the Claude-Code hooks schema for our events.

    SessionStart + UserPromptSubmit are universal. PostCompaction is only
    wired for clients that accept `additionalContext` from compaction
    events (currently Devin; Claude Code rejects this output for
    PreCompact/PostCompact events as of 2026-05).
    """
    entries: dict[str, Any] = {
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"bash {start_path} {client}",
                        "timeout": 10,
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
                        "timeout": 10,
                        "tag": HOOK_TAG,
                    }
                ],
            }
        ],
    }
    if post_compaction_path is not None:
        entries["PostCompaction"] = [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"bash {post_compaction_path} {client}",
                        "timeout": 15,
                        "tag": HOOK_TAG,
                    }
                ],
            }
        ]
    return entries


# ---------------------------------------------------------------------------
# Cursor hooks (~/.cursor/hooks.json)
#
# Cursor's schema differs from the Claude-Code family:
#   - `{"version": 1, "hooks": {<event>: [{"command": "..."}]}}`
#   - event names are camelCase: `sessionStart`, `beforeSubmitPrompt`, ...
#   - entries are flat command dicts (no nested "hooks" array)
#   - only `sessionStart` supports context injection (`additional_context`);
#     `beforeSubmitPrompt` is side-effect-only (logs the ask + autoremember).
# Unknown keys (like our `tag`) are ignored by Cursor, so we tag entries for
# clean, surgical removal.
# ---------------------------------------------------------------------------


def _build_cursor_entries(client: str, start_path: Path, submit_path: Path) -> dict[str, Any]:
    return {
        "sessionStart": [
            {"command": f"bash {start_path} {client}", "timeout": 10, "tag": HOOK_TAG}
        ],
        "beforeSubmitPrompt": [
            {"command": f"bash {submit_path} {client}", "timeout": 10, "tag": HOOK_TAG}
        ],
    }


def _cursor_entry_is_ours(entry: dict) -> bool:
    if entry.get("tag") == HOOK_TAG:
        return True
    return HOOKS_DIRNAME in entry.get("command", "")


def _merge_cursor_hooks(existing: dict, new_entries: dict) -> dict:
    merged = dict(existing or {})
    for event, entries in new_entries.items():
        bucket = [e for e in list(merged.get(event, [])) if not _cursor_entry_is_ours(e)]
        bucket.extend(entries)
        merged[event] = bucket
    return merged


def _strip_cursor_hooks(existing: dict) -> dict:
    cleaned: dict = {}
    for event, entries in (existing or {}).items():
        kept = [e for e in entries if not _cursor_entry_is_ours(e)]
        if kept:
            cleaned[event] = kept
    return cleaned


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
    """Install hooks into ~/.config/devin/config.json.

    Devin gets SessionStart + UserPromptSubmit + PostCompaction. Devin's
    PostCompaction event accepts `additionalContext` so we use it to
    re-inject the live working ledger after compaction completes.
    """
    client = "devin"
    hooks_dir = _install_dir_for(client)
    start_path = _render_script("session-start", client, hooks_dir / "session-start.sh")
    submit_path = _render_script("user-prompt-submit", client, hooks_dir / "user-prompt-submit.sh")
    post_path = _render_script("post-compaction", client, hooks_dir / "post-compaction.sh")

    cfg_path = Path.home() / ".config" / "devin" / "config.json"
    _backup(cfg_path)
    cfg = _load_json(cfg_path)
    cfg["hooks"] = _merge_hooks(
        cfg.get("hooks", {}),
        _build_claude_format_entries(client, start_path, submit_path, post_compaction_path=post_path),
    )
    _write_json(cfg_path, cfg)
    return {
        "client": client,
        "config": str(cfg_path),
        "scripts": [str(start_path), str(submit_path), str(post_path)],
    }


def install_for_claude_code() -> dict[str, Any]:
    """Install hooks into ~/.claude/settings.json (preferred) or ~/.claude.json.

    Claude Code gets SessionStart + UserPromptSubmit only. Its PreCompact /
    PostCompact events do NOT support `additionalContext` (community
    issues open as of 2026-05), so we rely on UserPromptSubmit to refresh
    the model's view of the WORKING block on the message that follows a
    compaction.
    """
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


def install_for_antigravity() -> dict[str, Any]:
    """Install hooks into ~/.gemini/antigravity/settings.json.

    Antigravity gets SessionStart + UserPromptSubmit.
    """
    client = "antigravity"
    hooks_dir = _install_dir_for(client)
    start_path = _render_script("session-start", client, hooks_dir / "session-start.sh")
    submit_path = _render_script("user-prompt-submit", client, hooks_dir / "user-prompt-submit.sh")

    cfg_path = Path.home() / ".gemini" / "antigravity" / "settings.json"
    _backup(cfg_path)
    cfg = _load_json(cfg_path)
    cfg["hooks"] = _merge_hooks(
        cfg.get("hooks", {}),
        _build_claude_format_entries(client, start_path, submit_path),
    )
    _write_json(cfg_path, cfg)
    return {
        "client": client,
        "config": str(cfg_path),
        "scripts": [str(start_path), str(submit_path)],
    }


def install_for_cursor() -> dict[str, Any]:
    """Install hooks into ~/.cursor/hooks.json.

    Cursor gets sessionStart + beforeSubmitPrompt. sessionStart injects the
    memory protocol + live working ledger + top fragments via
    `additional_context` (the only Cursor event that supports injection).
    beforeSubmitPrompt is side-effect-only — it logs the ask and runs
    autoremember so the always-on ~/.cursor/rules/hippocampus.mdc rule stays
    current; Cursor's beforeSubmitPrompt output schema cannot inject context.
    """
    client = "cursor"
    hooks_dir = _install_dir_for(client)
    start_path = _render_script("cursor-session-start", client, hooks_dir / "session-start.sh")
    submit_path = _render_script("cursor-before-submit", client, hooks_dir / "before-submit.sh")

    cfg_path = Path.home() / ".cursor" / "hooks.json"
    _backup(cfg_path)
    cfg = _load_json(cfg_path)
    cfg.setdefault("version", 1)
    cfg["hooks"] = _merge_cursor_hooks(
        cfg.get("hooks", {}),
        _build_cursor_entries(client, start_path, submit_path),
    )
    _write_json(cfg_path, cfg)
    return {"client": client, "config": str(cfg_path), "scripts": [str(start_path), str(submit_path)]}


def install_all() -> list[dict[str, Any]]:
    return [
        install_for_devin(),
        install_for_claude_code(),
        install_for_antigravity(),
        install_for_cursor(),
    ]


def uninstall_all() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for cfg_path in (
        Path.home() / ".config" / "devin" / "config.json",
        Path.home() / ".claude" / "settings.json",
        Path.home() / ".gemini" / "antigravity" / "settings.json",
    ):
        if not cfg_path.exists():
            results.append({"config": str(cfg_path), "status": "missing"})
            continue
        _backup(cfg_path)
        cfg = _load_json(cfg_path)
        cfg["hooks"] = _strip_hooks(cfg.get("hooks", {}))
        _write_json(cfg_path, cfg)
        results.append({"config": str(cfg_path), "status": "stripped"})

    # Cursor uses its own flat schema — strip with the Cursor-aware helper.
    cursor_cfg = Path.home() / ".cursor" / "hooks.json"
    if not cursor_cfg.exists():
        results.append({"config": str(cursor_cfg), "status": "missing"})
    else:
        _backup(cursor_cfg)
        cfg = _load_json(cursor_cfg)
        cfg["hooks"] = _strip_cursor_hooks(cfg.get("hooks", {}))
        _write_json(cursor_cfg, cfg)
        results.append({"config": str(cursor_cfg), "status": "stripped"})
    return results


def status() -> list[dict[str, Any]]:
    """Return a report per client of whether hooks are installed.

    Devin reports SessionStart + UserPromptSubmit + PostCompaction.
    Claude Code reports SessionStart + UserPromptSubmit only — its
    PostCompact event doesn't accept our output type.
    Pi reports session_start + before_agent_start + session_shutdown,
    which are wired by the bundled TypeScript extension (not a shell
    hook), so "installed" here means the extension file exists.
    """
    reports: list[dict[str, Any]] = []
    for client, cfg_path, expected_events in (
        (
            "devin",
            Path.home() / ".config" / "devin" / "config.json",
            ("SessionStart", "UserPromptSubmit", "PostCompaction"),
        ),
        (
            "claude-code",
            Path.home() / ".claude" / "settings.json",
            ("SessionStart", "UserPromptSubmit"),
        ),
        (
            "antigravity",
            Path.home() / ".gemini" / "antigravity" / "settings.json",
            ("SessionStart", "UserPromptSubmit"),
        ),
    ):
        data = _load_json(cfg_path) if cfg_path.exists() else {}
        events = (data.get("hooks") or {})
        installed = {
            ev: any(_entry_is_ours(e) for e in events.get(ev, []))
            for ev in expected_events
        }
        reports.append({"client": client, "config": str(cfg_path), "installed": installed})

    # Cursor — flat command-dict schema; only sessionStart can inject context,
    # beforeSubmitPrompt is side-effect-only. No PostCompaction equivalent.
    cursor_cfg = Path.home() / ".cursor" / "hooks.json"
    cursor_data = _load_json(cursor_cfg) if cursor_cfg.exists() else {}
    cursor_events = (cursor_data.get("hooks") or {})
    reports.append({
        "client": "cursor",
        "config": str(cursor_cfg),
        "installed": {
            ev: any(_cursor_entry_is_ours(e) for e in cursor_events.get(ev, []))
            for ev in ("sessionStart", "beforeSubmitPrompt")
        },
    })

    # Pi — extension-based, not shell-hook-based.
    pi_index = Path.home() / ".pi" / "agent" / "extensions" / "hippocampus" / "index.ts"
    pi_installed = pi_index.exists()
    reports.append({
        "client": "pi",
        "config": str(pi_index.parent),
        "installed": {
            "session_start": pi_installed,
            "before_agent_start": pi_installed,
            "session_shutdown": pi_installed,
        },
    })
    return reports
