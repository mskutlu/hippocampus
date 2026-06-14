"""Unit tests for client MCP registration formats."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


def _reload_clients_modules() -> None:
    # Pop both submodules AND the parent package so `from hippocampus.clients
    # import registry` triggers a fresh import instead of grabbing the stale
    # attribute that the still-cached parent package holds.
    for mod_name in list(sys.modules):
        if mod_name == "hippocampus.clients" or mod_name.startswith("hippocampus.clients."):
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


def test_register_cursor_uses_mcp_servers_schema(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("HIPPOCAMPUS_MCP_CMD", "/opt/fake/bin/hippocampus-mcp")
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    _reload_clients_modules()

    from hippocampus.clients import mcp_config, registry

    importlib.reload(registry)
    importlib.reload(mcp_config)

    spec = registry.by_name("cursor")
    assert spec is not None
    assert spec.mcp_config_format == "cursor-mcp-json"
    assert spec.rules_path == fake_home / ".cursor" / "rules" / "hippocampus.mdc"
    assert spec.mcp_config_path == fake_home / ".cursor" / "mcp.json"
    assert "alwaysApply: true" in spec.creation_header

    changed, _ = mcp_config.register(spec)
    assert changed is True

    data = json.loads(spec.mcp_config_path.read_text(encoding="utf-8"))
    entry = data["mcpServers"]["hippocampus"]
    assert entry["command"] == "/opt/fake/bin/hippocampus-mcp"
    assert entry["env"]["HIPPOCAMPUS_CLIENT"] == "cursor"
    # Cursor uses the bare mcpServers shape (no "type": "stdio" wrapper).
    assert "type" not in entry
    assert mcp_config.is_registered(spec) is True

    # Idempotent + surgical unregister.
    changed_again, _ = mcp_config.register(spec)
    assert changed_again is False
    removed, _ = mcp_config.unregister(spec)
    assert removed is True
    assert mcp_config.is_registered(spec) is False


def test_register_opencode_uses_mcp_schema_and_removes_legacy_mcpservers(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("HIPPOCAMPUS_MCP_CMD", "/opt/fake/bin/hippocampus-mcp")
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    _reload_clients_modules()

    from hippocampus.clients import mcp_config, registry

    importlib.reload(registry)
    importlib.reload(mcp_config)

    spec = registry.by_name("opencode")
    assert spec is not None
    assert spec.mcp_config_format == "opencode-json"
    assert spec.mcp_config_path == fake_home / ".config" / "opencode" / "opencode.json"

    spec.mcp_config_path.parent.mkdir(parents=True, exist_ok=True)
    spec.mcp_config_path.write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "mcp": {
                    "existing": {
                        "type": "local",
                        "command": ["existing-mcp"],
                        "enabled": True,
                    }
                },
                "mcpServers": {
                    "hippocampus": {
                        "command": "/old/hippocampus-mcp",
                        "args": [],
                        "env": {"HIPPOCAMPUS_CLIENT": "opencode"},
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    changed, _ = mcp_config.register(spec)
    assert changed is True

    data = json.loads(spec.mcp_config_path.read_text(encoding="utf-8"))
    assert "mcpServers" not in data
    assert "existing" in data["mcp"]
    entry = data["mcp"]["hippocampus"]
    assert entry == {
        "type": "local",
        "enabled": True,
        "timeout": 30000,
        "command": ["/opt/fake/bin/hippocampus-mcp"],
        "environment": {"HIPPOCAMPUS_CLIENT": "opencode"},
    }
    assert mcp_config.is_registered(spec) is True

    changed_again, _ = mcp_config.register(spec)
    assert changed_again is False

    removed, _ = mcp_config.unregister(spec)
    assert removed is True
    data_after = json.loads(spec.mcp_config_path.read_text(encoding="utf-8"))
    assert "hippocampus" not in data_after["mcp"]
    assert "existing" in data_after["mcp"]


def test_register_codex_uses_config_toml(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("HIPPOCAMPUS_MCP_CMD", "/opt/fake/bin/hippocampus-mcp")
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    _reload_clients_modules()

    from hippocampus.clients import mcp_config, registry

    importlib.reload(registry)
    importlib.reload(mcp_config)

    spec = registry.by_name("codex")
    assert spec is not None
    assert spec.mcp_config_format == "codex-toml"
    assert spec.rules_path == fake_home / ".codex" / "AGENTS.md"
    assert spec.mcp_config_path == fake_home / ".codex" / "config.toml"

    spec.mcp_config_path.parent.mkdir(parents=True, exist_ok=True)
    spec.mcp_config_path.write_text(
        'model = "gpt-5"\n\n[mcp_servers.existing]\ncommand = "npx"\nargs = ["server"]\n',
        encoding="utf-8",
    )

    changed, _ = mcp_config.register(spec)
    assert changed is True

    import tomllib

    data = tomllib.loads(spec.mcp_config_path.read_text(encoding="utf-8"))
    assert data["mcp_servers"]["existing"]["command"] == "npx"
    entry = data["mcp_servers"]["hippocampus"]
    assert entry["command"] == "/opt/fake/bin/hippocampus-mcp"
    assert entry["args"] == []
    assert entry["env"]["HIPPOCAMPUS_CLIENT"] == "codex"
    assert mcp_config.is_registered(spec) is True

    changed_again, _ = mcp_config.register(spec)
    assert changed_again is False

    removed, _ = mcp_config.unregister(spec)
    assert removed is True
    data_after = tomllib.loads(spec.mcp_config_path.read_text(encoding="utf-8"))
    assert "hippocampus" not in data_after["mcp_servers"]
    assert "existing" in data_after["mcp_servers"]


def test_register_pi_installs_extension(tmp_path, monkeypatch):
    """Pi has no native MCP — we install a TypeScript extension instead.

    Verifies the extension files land in ~/.pi/agent/extensions/hippocampus/,
    placeholders are rendered, and is_registered() flips to True.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("HIPPOCAMPUS_MCP_CMD", "/opt/fake/bin/hippocampus-mcp")
    monkeypatch.setenv("HIPPOCAMPUS_HIPPO_BIN", "/opt/fake/bin/hippo")
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    _reload_clients_modules()

    from hippocampus.clients import mcp_config, registry

    importlib.reload(registry)
    importlib.reload(mcp_config)

    spec = registry.by_name("pi")
    assert spec is not None
    assert spec.mcp_config_format == "pi-extension"
    assert spec.rules_path == fake_home / ".pi" / "agent" / "AGENTS.md"
    assert spec.mcp_config_path == fake_home / ".pi" / "agent" / "extensions" / "hippocampus"
    assert mcp_config.is_registered(spec) is False

    changed, msg = mcp_config.register(spec)
    assert changed is True, msg

    index = spec.mcp_config_path / "index.ts"
    assert index.exists()
    body = index.read_text(encoding="utf-8")
    # Placeholders rendered with the absolute paths
    assert "__HIPPO_BIN__" not in body
    assert "__HIPPOCAMPUS_MCP__" not in body
    assert "__HIPPOCAMPUS_CLIENT__" not in body
    assert "/opt/fake/bin/hippo" in body
    assert "/opt/fake/bin/hippocampus-mcp" in body
    # Client tag baked in correctly
    assert 'const HIPPOCAMPUS_CLIENT = "pi"' in body

    # Idempotent — second register is a no-op
    changed_again, _ = mcp_config.register(spec)
    assert changed_again is False

    assert mcp_config.is_registered(spec) is True

    # Unregister removes files but is surgical (only touches what we wrote)
    removed, _ = mcp_config.unregister(spec)
    assert removed is True
    assert mcp_config.is_registered(spec) is False
