"""`claude` shell alias that opts every session into the hippocampus channel."""

from __future__ import annotations

import os
from pathlib import Path

ALIAS_LINE = "alias claude='claude --channels plugin:hippocampus@hippocampus'"
BEGIN, END = "# >>> hippocampus a2a >>>", "# <<< hippocampus a2a <<<"


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
    new = (base.rstrip("\n") + "\n\n" if base.strip() else "") + f"{BEGIN}\n{ALIAS_LINE}\n{END}\n"
    if new == old:
        return False
    rc.write_text(new, encoding="utf-8")
    return True


def uninstall(rc: Path) -> bool:
    if not rc.exists() or BEGIN not in (old := rc.read_text(encoding="utf-8")):
        return False
    rc.write_text(_strip(old), encoding="utf-8")
    return True
