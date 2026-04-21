"""Client registry.

Each registered client is a tuple of (name, default_path, header-if-creating,
mcp-server-config-path-hint). The paths are defaults; users can override via
CLI flags or env vars when something is installed in an unusual place.

The registry is deliberately simple so we can add new clients (Cursor,
continue.dev, Zed, ...) by appending one line.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hippocampus import config  # noqa: F401  (re-exported by adapters)


@dataclass(frozen=True)
class ClientSpec:
    name: str                       # stable id ('devin', 'claude-code', ...)
    label: str                      # human-friendly name
    rules_path: Path                # file receiving the injection block
    creation_header: str            # header used when creating a fresh rules file
    mcp_config_path: Path | None    # where to register the MCP server (optional)
    mcp_config_format: str          # 'devin-json' | 'claude-json' | 'windsurf-mcp' | 'opencode-json'

    @property
    def exists(self) -> bool:
        return self.rules_path.exists()


HOME = Path.home()

CLIENTS: list[ClientSpec] = [
    ClientSpec(
        name="devin",
        label="Devin CLI",
        rules_path=HOME / ".config" / "devin" / "AGENTS.md",
        creation_header="# Devin Global Rules",
        mcp_config_path=HOME / ".config" / "devin" / "config.json",
        mcp_config_format="devin-json",
    ),
    ClientSpec(
        name="claude-code",
        label="Claude Code",
        rules_path=HOME / ".claude" / "CLAUDE.md",
        creation_header="# Claude Code Global Rules",
        mcp_config_path=HOME / ".claude.json",
        mcp_config_format="claude-json",
    ),
    ClientSpec(
        name="opencode",
        label="OpenCode",
        rules_path=HOME / ".config" / "opencode" / "AGENTS.md",
        creation_header="# OpenCode Global Rules",
        mcp_config_path=HOME / ".config" / "opencode" / "opencode.json",
        mcp_config_format="opencode-json",
    ),
    ClientSpec(
        name="windsurf",
        label="Windsurf",
        rules_path=HOME / ".codeium" / "windsurf" / "memories" / "global_rules.md",
        creation_header="# Windsurf Global Rules",
        mcp_config_path=HOME / ".codeium" / "windsurf" / "mcp_config.json",
        mcp_config_format="windsurf-mcp",
    ),
    ClientSpec(
        name="antigravity",
        label="Antigravity",
        rules_path=HOME / ".antigravity" / "rules" / "global_rules.md",
        creation_header="# Antigravity Global Rules",
        mcp_config_path=HOME / ".antigravity" / "mcp_config.json",
        mcp_config_format="windsurf-mcp",
    ),
]


def by_name(name: str) -> ClientSpec | None:
    for spec in CLIENTS:
        if spec.name == name:
            return spec
    return None
