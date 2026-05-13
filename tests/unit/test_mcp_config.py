"""Unit tests for client MCP registration formats."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


def _reload_clients_modules() -> None:
    for mod_name in list(sys.modules):
        if mod_name.startswith("hippocampus.clients.registry") or mod_name.startswith("hippocampus.clients.mcp_config"):
            sys.modules.pop(mod_name)


def test_register_vscode_copilot_uses_vscode_schema(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("HIPPOCAMPUS_MCP_CMD", "/opt/fake/bin/hippocampus-mcp")
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    _reload_clients_modules()

    from hippocampus.clients import mcp_config, registry

    importlib.reload(registry)
    importlib.reload(mcp_config)

    spec = registry.by_name("vscode-copilot")
    assert spec is not None
    assert spec.rules_path == fake_home / ".copilot" / "instructions" / "hippocampus.instructions.md"
    assert 'applyTo: "**"' in spec.creation_header

    changed, _ = mcp_config.register(spec)
    assert changed is True

    data = json.loads(spec.mcp_config_path.read_text(encoding="utf-8"))
    entry = data["servers"]["hippocampus"]
    assert entry["type"] == "stdio"
    assert entry["command"] == "/opt/fake/bin/hippocampus-mcp"
    assert entry["env"]["HIPPOCAMPUS_CLIENT"] == "vscode-copilot"
    assert mcp_config.is_registered(spec) is True