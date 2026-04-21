"""Shared injection plumbing.

Every client adapter renders the same marker-delimited block inside a
different file. Logic to locate an existing block and replace its body (or
append if missing) is centralised here so the adapters stay one-liners.

There are two independent marker blocks:
    long-term block:  <!-- HIPPOCAMPUS:START --> ... <!-- HIPPOCAMPUS:END -->
                      top-N fragments by (confidence × recency)
    working block:    <!-- HIPPOCAMPUS:WORKING:START --> ... END -->
                      current session's ledger (asks, dones, decisions, blockers)
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from hippocampus import config
from hippocampus.storage.fragments import Fragment
from hippocampus.storage.ledger import LedgerEntry, grouped_for_render

_WORKING_PROTOCOL = [
    "> **Working memory — ALWAYS up-to-date. Survives compaction.**",
    ">",
    "> **Memory protocol — call these tools as a reflex, not on request:**",
    "> - When the user asks something → `log_progress(kind=\"ask\", content=\"<one-line paraphrase>\")`",
    "> - When you complete an action  → `log_progress(kind=\"done\", content=\"<what you did>\")`",
    "> - When a decision is made      → `log_progress(kind=\"decision\", content=\"<decision>\")`",
    "> - When you hit a blocker       → `log_progress(kind=\"blocker\", content=\"<what's blocking>\")`",
    "> - When a next step is planned  → `log_progress(kind=\"next\", content=\"<next step>\")`",
    "> - When the goal is set/changed → `log_progress(kind=\"goal\", content=\"<the goal>\")`",
    ">",
    "> Use `get_progress()` to re-read the full ledger.",
    "> Use `end_progress(distill_to_fragment=True, summary=...)` when the task is complete.",
    ">",
    "> **This block is cross-session, cross-compaction, cross-client. Do not duplicate entries — the dedup window is 60s.**",
]


def _short(text: str, limit: int) -> str:
    text = " ".join(text.strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _fmt_time(iso_utc: str | None) -> str:
    """Return `HH:MM` UTC for compact rendering."""
    if not iso_utc:
        return "--:--"
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%H:%M")
    except ValueError:
        return "--:--"


# ---------------------------------------------------------------------------
# Long-term block
# ---------------------------------------------------------------------------


def format_injection_block(
    fragments: Iterable[Fragment],
    *,
    summary_max: int = config.INJECTION_SUMMARY_MAX_CHARS,
    header: str = "Hippocampus — auto-injected memory fragments",
) -> str:
    """Render the long-term block (top-N fragments)."""
    bullets: list[str] = []
    for f in fragments:
        conf = f"{f.confidence:.2f}"
        pin_mark = " 📌" if f.pinned else ""
        tags = f" [{', '.join(f.tags)}]" if f.tags else ""
        summary = _short(f.summary or f.content, summary_max)
        bullets.append(f"- `{f.id}` (conf={conf}){pin_mark}{tags} — {summary}")

    body_lines: list[str] = [
        config.INJECTION_MARKER_START,
        "",
        f"## {header}",
        "",
        "> These are the highest-ranking memories right now. They are always in context.",
        "> Call the `recall` MCP tool when you need more detail — each call boosts confidence.",
        "> Call `remember` when you learn something worth keeping across sessions/clients.",
        "",
    ]
    body_lines.extend(bullets if bullets else ["_(no fragments stored yet)_"])
    body_lines.extend(["", config.INJECTION_MARKER_END])
    return "\n".join(body_lines) + "\n"


# ---------------------------------------------------------------------------
# Working block
# ---------------------------------------------------------------------------


def format_working_block(
    *,
    session_id: str | None,
    client: str,
    started_at: str | None,
    entries: Iterable[LedgerEntry] | None,
    content_max: int = config.WORKING_CONTENT_MAX_CHARS,
) -> str:
    """Render the per-session working-memory block."""
    lines: list[str] = [
        config.WORKING_MARKER_START,
        "",
        "## Hippocampus — current session",
        "",
    ]
    lines.extend(_WORKING_PROTOCOL)
    lines.append("")

    if not session_id or not entries:
        lines.append(
            f"**Session**: _(no active session for `{client}` — the next `log_progress` call starts one)_"
        )
        lines.extend(["", config.WORKING_MARKER_END])
        return "\n".join(lines) + "\n"

    grouped = grouped_for_render(
        entries,
        max_asks=config.WORKING_MAX_ASKS,
        max_dones=config.WORKING_MAX_DONES,
        max_next=config.WORKING_MAX_NEXT,
        max_notes=config.WORKING_MAX_NOTES,
    )

    lines.append(
        f"**Session**: `{session_id}` · client: `{client}` · started: {_fmt_time(started_at)} UTC · turn: {grouped['turn_count']}"
    )
    lines.append("")

    # Goal
    if grouped["goal"]:
        g = grouped["goal"]
        lines.append(f"### Goal")
        lines.append(f"- [{_fmt_time(g.created_at)}] {_short(g.content, content_max)}")
        lines.append("")

    def _section(title: str, items: list[LedgerEntry]) -> None:
        if not items:
            return
        lines.append(f"### {title}")
        for e in items:
            prefix = "✗" if (e.kind == "blocker" and not e.resolved) else "-"
            lines.append(f"{prefix} [{_fmt_time(e.created_at)}] {_short(e.content, content_max)}")
        lines.append("")

    _section("Asks (latest)", grouped["asks"])
    _section("Completed", grouped["dones"])
    _section("Blockers (open)", grouped["blockers"])
    _section("Decisions", grouped["decisions"])
    _section("Next", grouped["nexts"])
    _section("Notes", grouped["notes"])

    lines.append(config.WORKING_MARKER_END)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Block upsert / removal (generalised to any marker pair)
# ---------------------------------------------------------------------------


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _ensure_pristine_backup(path: Path) -> None:
    """Create a one-time `.pre-hippocampus.bak` next to `path` if missing."""
    if not path.exists():
        return
    bak = path.with_suffix(path.suffix + ".pre-hippocampus.bak")
    if bak.exists():
        return
    shutil.copy2(path, bak)


def upsert_block(
    path: Path,
    block: str,
    *,
    create_if_missing: bool = True,
    header_when_creating: str | None = None,
    marker_start: str | None = None,
    marker_end: str | None = None,
) -> tuple[bool, str]:
    """Insert or replace a marker-delimited block inside `path`.

    The start/end markers default to the long-term block's markers; override
    via keyword args for the working block.
    """
    start = marker_start or config.INJECTION_MARKER_START
    end = marker_end or config.INJECTION_MARKER_END

    if not path.exists():
        if not create_if_missing:
            return False, "target file missing and create_if_missing=False"
        path.parent.mkdir(parents=True, exist_ok=True)
        header = header_when_creating or ""
        new_content = (header + ("\n\n" if header else "") + block).rstrip() + "\n"
        path.write_text(new_content, encoding="utf-8")
        return True, "created"

    existing = path.read_text(encoding="utf-8")
    start_idx = existing.find(start)
    end_idx = existing.find(end)
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        before = existing[:start_idx]
        after = existing[end_idx + len(end) :]
        before = before.rstrip() + ("\n\n" if before.strip() else "")
        after = ("\n\n" + after.lstrip("\n")) if after.strip() else "\n"
        candidate = before + block.rstrip() + after
    else:
        candidate = existing.rstrip() + "\n\n" + block.rstrip() + "\n"

    if _hash(candidate) == _hash(existing):
        return False, "unchanged"

    _ensure_pristine_backup(path)
    path.write_text(candidate, encoding="utf-8")
    return True, "replaced" if start_idx != -1 else "appended"


def upsert_working_block(path: Path, block: str, *, create_if_missing: bool = True, header_when_creating: str | None = None) -> tuple[bool, str]:
    return upsert_block(
        path, block,
        create_if_missing=create_if_missing,
        header_when_creating=header_when_creating,
        marker_start=config.WORKING_MARKER_START,
        marker_end=config.WORKING_MARKER_END,
    )


def remove_block(
    path: Path,
    *,
    marker_start: str | None = None,
    marker_end: str | None = None,
) -> bool:
    """Strip a marker block (idempotent)."""
    start = marker_start or config.INJECTION_MARKER_START
    end = marker_end or config.INJECTION_MARKER_END
    if not path.exists():
        return False
    existing = path.read_text(encoding="utf-8")
    start_idx = existing.find(start)
    end_idx = existing.find(end)
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return False
    before = existing[:start_idx].rstrip() + "\n"
    after = existing[end_idx + len(end) :].lstrip("\n")
    cleaned = before + ("\n" + after if after else "")
    path.write_text(cleaned, encoding="utf-8")
    return True


def remove_working_block(path: Path) -> bool:
    return remove_block(
        path,
        marker_start=config.WORKING_MARKER_START,
        marker_end=config.WORKING_MARKER_END,
    )
