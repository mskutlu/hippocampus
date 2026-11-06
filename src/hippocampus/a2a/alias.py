"""`claude` shell function that opts every session into the hippocampus channel."""

from __future__ import annotations

import os
from pathlib import Path

BEGIN, END = "# >>> hippocampus a2a >>>", "# <<< hippocampus a2a <<<"
# --channels is variadic: it goes LAST so it can't swallow a subcommand or prompt; subcommands skip it.
# shortcut: subcommand list is from Claude Code 2.1.319 `--help`, refresh when new ones ship.
FUNCTION = """unalias claude 2>/dev/null
claude() {
  case "${1:-}" in
    agents|attach|auth|auto-mode|doctor|gateway|import|install|logs|mcp|plugin|plugins|purge|respawn|rm|setup-token|stop|kill|ultrareview|update|upgrade|-p|--print|-v|--version|-h|--help)
      command claude "$@" ;;
    *)
      command claude "$@" --channels plugin:hippocampus@hippocampus ;;
  esac
}"""
BLOCK = f"{BEGIN}\n{FUNCTION}\n{END}\n"


def rc_file() -> Path:
    home = Path.home()
    if os.path.basename(os.environ.get("SHELL", "")) == "bash":
        return home / ".bash_profile" if not (home / ".bashrc").exists() and (home / ".bash_profile").exists() else home / ".bashrc"
    return home / ".zshrc"


def _strip(text: str) -> str:
    start = text.find(BEGIN)
    end = text.find(END, start)
    if start < 0 or end < 0:
        return text
    return (text[:start].rstrip("\n") + "\n" + text[end + len(END):].lstrip("\n")).lstrip("\n")


def install(rc: Path) -> bool:
    """True if the file changed."""
    old = rc.read_text(encoding="utf-8") if rc.exists() else ""
    base = _strip(old)
    new = (base.rstrip("\n") + "\n\n" if base.strip() else "") + BLOCK
    if new == old:
        return False
    rc.write_text(new, encoding="utf-8")
    return True


def uninstall(rc: Path) -> bool:
    if not rc.exists() or BEGIN not in (old := rc.read_text(encoding="utf-8")):
        return False
    rc.write_text(_strip(old), encoding="utf-8")
    return True
