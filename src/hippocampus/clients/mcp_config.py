"""Register the Hippocampus MCP server in each client's MCP config file.

Each AI client uses a different config schema. This module centralises the
format-specific knowledge so adding a new client is just one new branch.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hippocampus.clients.registry import CLIENTS, ClientSpec

MCP_ENTRY_NAME = "hippocampus"


def _hippocampus_command() -> dict[str, Any]:
    """Return the command payload used by most clients to spawn the server.

    We call the installed `hippocampus-mcp` script so no absolute path to the
    repo is required. Users can override by setting HIPPOCAMPUS_MCP_CMD.
    """
    import os
    import shutil as _shutil

    override = os.environ.get("HIPPOCAMPUS_MCP_CMD")
    if override:
        # Expect a space-separated command; split only the first element as command.
        parts = override.split()
        return {"command": parts[0], "args": parts[1:]}

    cmd = _shutil.which("hippocampus-mcp")
    if cmd:
        return {"command": cmd, "args": []}

    # Fallback: run via uv from the source checkout.
    here = Path(__file__).resolve().parents[3]
    return {"command": "uv", "args": ["run", "--project", str(here), "hippocampus-mcp"]}


def _backup(path: Path) -> Path:
    if not path.exists():
        return path
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_suffix(path.suffix + f".{stamp}.bak")
    shutil.copy2(path, backup)
    return backup


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_json(path: Path, data: dict) -> None:
    _ensure_dir(path)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def register(spec: ClientSpec) -> tuple[bool, str]:
    """Register the Hippocampus MCP server in one client's config.

    Returns (changed, message).
    """
    cmd = _hippocampus_command()
    fmt = spec.mcp_config_format
    path = spec.mcp_config_path
    if path is None:
        return False, f"{spec.name}: no MCP config path configured"

    _backup(path)
    _ensure_dir(path)
    data = _load_json(path)

    if fmt in ("devin-json", "claude-json", "windsurf-mcp", "opencode-json"):
        # All four use the same mcpServers schema at the top level.
        data.setdefault("mcpServers", {})
        existing = data["mcpServers"].get(MCP_ENTRY_NAME)
        # env is passed through by every client's MCP transport; the server
        # reads HIPPOCAMPUS_CLIENT to correctly scope session tracking.
        new_entry = {
            "command": cmd["command"],
            "args": cmd["args"],
            "env": {"HIPPOCAMPUS_CLIENT": spec.name},
        }
        if existing == new_entry:
            return False, f"{spec.name}: already registered at {path}"
        data["mcpServers"][MCP_ENTRY_NAME] = new_entry
        _write_json(path, data)
        return True, f"{spec.name}: registered at {path}"

    return False, f"{spec.name}: unknown mcp_config_format {fmt!r}"


def unregister(spec: ClientSpec) -> tuple[bool, str]:
    path = spec.mcp_config_path
    if path is None or not path.exists():
        return False, f"{spec.name}: no config to clean"
    data = _load_json(path)
    servers = data.get("mcpServers", {})
    if MCP_ENTRY_NAME not in servers:
        return False, f"{spec.name}: entry not present"
    servers.pop(MCP_ENTRY_NAME, None)
    data["mcpServers"] = servers
    _write_json(path, data)
    return True, f"{spec.name}: removed from {path}"


def register_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    for spec in CLIENTS:
        try:
            changed, msg = register(spec)
            results.append((spec.name, changed, msg))
        except Exception as e:  # noqa: BLE001
            results.append((spec.name, False, f"error: {e}"))
    return results
