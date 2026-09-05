"""Register the Hippocampus MCP server in each client's MCP config file.

Each AI client uses a different config schema. This module centralises the
format-specific knowledge so adding a new client is just one new branch.
"""

from __future__ import annotations

import json
import shutil
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

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


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    _ensure_dir(path)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(v) for v in values) + "]"


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return {}


def _remove_codex_mcp_tables(text: str) -> str:
    lines = text.splitlines()
    kept: list[str] = []
    skip = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            table = stripped.strip("[]").strip()
            skip = table == f"mcp_servers.{MCP_ENTRY_NAME}" or table == f"mcp_servers.{MCP_ENTRY_NAME}.env"
        if not skip:
            kept.append(line)
    return "\n".join(kept).rstrip()


def _codex_entry_toml(spec: ClientSpec, cmd: dict[str, Any]) -> str:
    env = {"HIPPOCAMPUS_CLIENT": spec.name}
    lines = [
        f"[mcp_servers.{MCP_ENTRY_NAME}]",
        f"command = {_toml_string(cmd['command'])}",
        f"args = {_toml_array(cmd['args'])}",
        "",
        f"[mcp_servers.{MCP_ENTRY_NAME}.env]",
    ]
    lines.extend(f"{key} = {_toml_string(value)}" for key, value in sorted(env.items()))
    return "\n".join(lines)


def _codex_entry_data(spec: ClientSpec, cmd: dict[str, Any]) -> dict[str, Any]:
    return {
        "command": cmd["command"],
        "args": cmd["args"],
        "env": {"HIPPOCAMPUS_CLIENT": spec.name},
    }


def _install_codex_toml(spec: ClientSpec) -> tuple[bool, str]:
    path = spec.mcp_config_path
    if path is None:
        return False, f"{spec.name}: no MCP config path configured"

    cmd = _hippocampus_command()
    desired = _codex_entry_data(spec, cmd)
    existing_data = _load_toml(path)
    existing = (existing_data.get("mcp_servers") or {}).get(MCP_ENTRY_NAME)
    if existing == desired:
        return False, f"{spec.name}: already registered at {path}"

    _backup(path)
    _ensure_dir(path)
    existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
    base = _remove_codex_mcp_tables(existing_text)
    body = _codex_entry_toml(spec, cmd)
    new_text = (base + "\n\n" + body if base else body).rstrip() + "\n"
    path.write_text(new_text, encoding="utf-8")
    return True, f"{spec.name}: registered at {path}"


def _codex_toml_is_registered(spec: ClientSpec) -> bool:
    path = spec.mcp_config_path
    if path is None or not path.exists():
        return False
    data = _load_toml(path)
    return MCP_ENTRY_NAME in (data.get("mcp_servers") or {})


def _uninstall_codex_toml(spec: ClientSpec) -> tuple[bool, str]:
    path = spec.mcp_config_path
    if path is None or not path.exists():
        return False, f"{spec.name}: no config to clean"
    text = path.read_text(encoding="utf-8")
    cleaned = _remove_codex_mcp_tables(text).rstrip() + "\n"
    if cleaned == text:
        return False, f"{spec.name}: entry not present"
    _backup(path)
    path.write_text(cleaned, encoding="utf-8")
    return True, f"{spec.name}: removed from {path}"


def _server_bucket(data: dict, fmt: str) -> dict[str, Any] | None:
    if fmt == "vscode-mcp-json":
        data.setdefault("servers", {})
        return data["servers"]
    if fmt == "opencode-json":
        data.setdefault("mcp", {})
        return data["mcp"]
    if fmt == "zcode-json":
        data.setdefault("mcp", {})
        data["mcp"].setdefault("servers", {})
        return data["mcp"]["servers"]
    if fmt == "zed-json":
        data.setdefault("context_servers", {})
        return data["context_servers"]
    if fmt in ("devin-json", "claude-json", "windsurf-mcp", "cursor-mcp-json"):
        data.setdefault("mcpServers", {})
        return data["mcpServers"]
    return None


def _remove_legacy_opencode_mcpservers(data: dict[str, Any]) -> bool:
    """Remove the invalid OpenCode mcpServers.hippocampus entry from older releases."""
    legacy = data.get("mcpServers")
    if not isinstance(legacy, dict) or MCP_ENTRY_NAME not in legacy:
        return False
    legacy.pop(MCP_ENTRY_NAME, None)
    if not legacy:
        data.pop("mcpServers", None)
    return True


def _pi_extension_template_dir() -> Path:
    """Path to the source extension directory bundled in the repo."""
    packaged = Path(__file__).resolve().parents[1] / "assets" / "pi-extension"
    if packaged.exists():
        return packaged
    return Path(__file__).resolve().parents[3] / "scripts" / "pi-extension"


def _pi_extension_index(spec: ClientSpec) -> Path:
    """Where the installed extension's entry file lives."""
    return spec.mcp_config_path / "index.ts" if spec.mcp_config_path else Path()


def _install_pi_extension(spec: ClientSpec) -> tuple[bool, str]:
    """Drop the Hippocampus Pi extension into ~/.pi/agent/extensions/hippocampus.

    Pi auto-discovers everything under ~/.pi/agent/extensions/, so simply
    placing the directory there is the entire registration step. We render
    `__HIPPO_BIN__` and `__HIPPOCAMPUS_MCP__` placeholders so the extension can
    spawn the MCP server without relying on PATH from inside Pi.
    """
    dest = spec.mcp_config_path
    if dest is None:
        return False, f"{spec.name}: no extension path configured"

    src = _pi_extension_template_dir()
    if not src.exists():
        return False, f"{spec.name}: bundled extension missing at {src}"

    cmd = _hippocampus_command()
    mcp_invocation = " ".join([cmd["command"], *cmd["args"]]).strip()

    import os as _os
    hippo_bin = _os.environ.get("HIPPOCAMPUS_HIPPO_BIN")
    if not hippo_bin:
        hippo_bin = shutil.which("hippo") or "hippo"

    dest.mkdir(parents=True, exist_ok=True)
    rendered: list[str] = []
    for entry in sorted(src.iterdir()):
        if entry.is_dir() or entry.name.startswith("."):
            continue
        target = dest / entry.name
        body = entry.read_text(encoding="utf-8")
        body = body.replace("__HIPPO_BIN__", hippo_bin)
        body = body.replace("__HIPPOCAMPUS_MCP__", mcp_invocation)
        body = body.replace("__HIPPOCAMPUS_CLIENT__", spec.name)
        # Idempotency — only touch the file when content changes
        if target.exists() and target.read_text(encoding="utf-8") == body:
            continue
        target.write_text(body, encoding="utf-8")
        rendered.append(target.name)

    # Tool manifest generated from the server's TOOL_SPECS so the extension
    # never drifts from the MCP surface (V11).
    import json as _json

    from hippocampus.mcp.server import tools_manifest

    manifest_path = dest / "tools.json"
    manifest_body = _json.dumps(tools_manifest(), indent=2, ensure_ascii=False) + "\n"
    if not manifest_path.exists() or manifest_path.read_text(encoding="utf-8") != manifest_body:
        manifest_path.write_text(manifest_body, encoding="utf-8")
        rendered.append(manifest_path.name)

    if rendered:
        return True, f"{spec.name}: installed extension at {dest} ({', '.join(rendered)})"
    return False, f"{spec.name}: extension already current at {dest}"


def _pi_extension_is_registered(spec: ClientSpec) -> bool:
    idx = _pi_extension_index(spec)
    return bool(idx) and idx.exists()


def _uninstall_pi_extension(spec: ClientSpec) -> tuple[bool, str]:
    dest = spec.mcp_config_path
    if dest is None or not dest.exists():
        return False, f"{spec.name}: no extension to remove"
    # Only remove files we installed; never recursive-delete a non-empty dir
    # the user might have touched.
    removed = 0
    for entry in dest.iterdir():
        if entry.is_dir():
            continue
        try:
            entry.unlink()
            removed += 1
        except OSError:
            pass
    try:
        dest.rmdir()
    except OSError:
        pass
    if removed:
        return True, f"{spec.name}: removed extension at {dest}"
    return False, f"{spec.name}: nothing to remove at {dest}"


def _hermes_entry(spec: ClientSpec, existing: Any = None) -> dict[str, Any]:
    """Build a Hermes ``mcp_servers.hippocampus`` entry.

    Hermes uses YAML and lets users add optional per-server controls (tool
    filters, timeouts, and trust settings). Keep those controls on refresh
    while ensuring the executable and client scope remain current.
    """
    cmd = _hippocampus_command()
    prior = existing if isinstance(existing, dict) else {}
    prior_env = prior.get("env") if isinstance(prior.get("env"), dict) else {}
    return {
        **prior,
        "command": cmd["command"],
        "args": cmd["args"],
        "env": {**prior_env, "HIPPOCAMPUS_CLIENT": spec.name},
        "enabled": True,
    }


def _hermes_is_registered(spec: ClientSpec) -> bool:
    path = spec.mcp_config_path
    if path is None or not path.exists():
        return False
    servers = _load_yaml(path).get("mcp_servers")
    return isinstance(servers, dict) and MCP_ENTRY_NAME in servers


def _install_hermes_yaml(spec: ClientSpec) -> tuple[bool, str]:
    path = spec.mcp_config_path
    if path is None:
        return False, f"{spec.name}: no MCP config path configured"

    data = _load_yaml(path)
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcp_servers"] = servers
    entry = _hermes_entry(spec, servers.get(MCP_ENTRY_NAME))
    if servers.get(MCP_ENTRY_NAME) == entry:
        return False, f"{spec.name}: already registered at {path}"

    _backup(path)
    servers[MCP_ENTRY_NAME] = entry
    _write_yaml(path, data)
    return True, f"{spec.name}: registered at {path}"


def _uninstall_hermes_yaml(spec: ClientSpec) -> tuple[bool, str]:
    path = spec.mcp_config_path
    if path is None or not path.exists():
        return False, f"{spec.name}: no config to clean"
    data = _load_yaml(path)
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict) or MCP_ENTRY_NAME not in servers:
        return False, f"{spec.name}: entry not present"

    _backup(path)
    servers.pop(MCP_ENTRY_NAME)
    if not servers:
        data.pop("mcp_servers", None)
    _write_yaml(path, data)
    return True, f"{spec.name}: removed from {path}"


def is_registered(spec: ClientSpec) -> bool:
    if spec.mcp_config_format == "codex-toml":
        return _codex_toml_is_registered(spec)
    if spec.mcp_config_format == "pi-extension":
        return _pi_extension_is_registered(spec)
    if spec.mcp_config_format == "hermes-yaml":
        return _hermes_is_registered(spec)
    path = spec.mcp_config_path
    if path is None or not path.exists():
        return False
    data = _load_json(path)
    bucket = _server_bucket(data, spec.mcp_config_format)
    if bucket is None:
        return False
    return MCP_ENTRY_NAME in bucket


def register(spec: ClientSpec) -> tuple[bool, str]:
    """Register the Hippocampus MCP server in one client's config.

    Returns (changed, message). For Pi (which has no native MCP support),
    this installs a TypeScript extension that bridges to the MCP server.
    """
    fmt = spec.mcp_config_format
    path = spec.mcp_config_path
    if path is None:
        return False, f"{spec.name}: no MCP config path configured"

    if fmt == "pi-extension":
        return _install_pi_extension(spec)
    if fmt == "codex-toml":
        return _install_codex_toml(spec)
    if fmt == "hermes-yaml":
        return _install_hermes_yaml(spec)

    cmd = _hippocampus_command()
    _backup(path)
    _ensure_dir(path)
    data = _load_json(path)

    bucket = _server_bucket(data, fmt)
    if bucket is None:
        return False, f"{spec.name}: unknown mcp_config_format {fmt!r}"

    legacy_changed = False
    if fmt == "opencode-json":
        legacy_changed = _remove_legacy_opencode_mcpservers(data)

    # env is passed through by every client's MCP transport; the server reads
    # HIPPOCAMPUS_CLIENT to correctly scope session tracking.
    new_entry = {
        "command": cmd["command"],
        "args": cmd["args"],
        "env": {"HIPPOCAMPUS_CLIENT": spec.name},
    }
    if fmt == "vscode-mcp-json":
        new_entry = {"type": "stdio", **new_entry}
    elif fmt == "opencode-json":
        new_entry = {
            "type": "local",
            "enabled": True,
            "timeout": 30000,
            "command": [cmd["command"], *cmd["args"]],
            "environment": {"HIPPOCAMPUS_CLIENT": spec.name},
        }

    existing = bucket.get(MCP_ENTRY_NAME)
    if fmt in ("zcode-json", "zed-json") and isinstance(existing, dict):
        # Preserve user-set extras (e.g. per-tool approval_mode, or Zed's
        # per-tool `tools` permission overrides) on re-register.
        new_entry = {**existing, **new_entry}
    if existing == new_entry and not legacy_changed:
        return False, f"{spec.name}: already registered at {path}"
    bucket[MCP_ENTRY_NAME] = new_entry
    _write_json(path, data)
    return True, f"{spec.name}: registered at {path}"


def unregister(spec: ClientSpec) -> tuple[bool, str]:
    if spec.mcp_config_format == "codex-toml":
        return _uninstall_codex_toml(spec)
    if spec.mcp_config_format == "pi-extension":
        return _uninstall_pi_extension(spec)
    if spec.mcp_config_format == "hermes-yaml":
        return _uninstall_hermes_yaml(spec)
    path = spec.mcp_config_path
    if path is None or not path.exists():
        return False, f"{spec.name}: no config to clean"
    data = _load_json(path)
    servers = _server_bucket(data, spec.mcp_config_format)
    if servers is None:
        return False, f"{spec.name}: unknown mcp_config_format {spec.mcp_config_format!r}"
    legacy_changed = False
    if spec.mcp_config_format == "opencode-json":
        legacy_changed = _remove_legacy_opencode_mcpservers(data)
    if MCP_ENTRY_NAME not in servers and not legacy_changed:
        return False, f"{spec.name}: entry not present"
    servers.pop(MCP_ENTRY_NAME, None)
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
