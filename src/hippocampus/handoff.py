"""Continuously-updated per-session handoff documents.

A handoff is a full, unabridged markdown snapshot of one session's ledger,
rewritten on every `log_progress` call. It is the durable on-disk anchor for
context-compaction recovery: rules-file blocks and hook payloads can be
summarized away or missed, but a short stable file path survives almost any
summarization — and the file itself is always current.

Unlike the WORKING block (newest-first, capped, truncated), the handoff is
chronological and complete: every entry keeps its full content and details.
Files live under `~/.hippocampus/handoffs/<session_id>.md` and are kept after
the session ends, forming a browsable history of past handoffs.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from hippocampus import config
from hippocampus.storage.ledger import LedgerEntry


def handoff_dir() -> Path:
    raw = config.get_setting("handoff_dir") or str(config.HIPPOCAMPUS_HOME / "handoffs")
    return Path(os.path.expanduser(str(raw)))


def handoff_path(session_id: str) -> Path:
    return handoff_dir() / f"{session_id}.md"


def enabled() -> bool:
    return bool(config.get_setting("handoff_enabled"))


def current_goal(entries: Iterable[LedgerEntry]) -> LedgerEntry | None:
    """The session's authoritative goal: latest `goal` entry, else first ask."""
    goals = [e for e in entries if e.kind == "goal"]
    if goals:
        return goals[-1]
    asks = [e for e in entries if e.kind == "ask"]
    return asks[0] if asks else None


def _fmt_ts(iso_utc: str | None) -> str:
    if not iso_utc:
        return "--:--"
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%m-%d %H:%M")
    except ValueError:
        return "--:--"


def _entry_lines(e: LedgerEntry) -> list[str]:
    lines = [f"- [{_fmt_ts(e.created_at)}] {e.content.strip()}"]
    if e.details and e.details.strip():
        for ln in e.details.strip().splitlines():
            lines.append(f"  {ln}")
    return lines


def render_handoff(
    *,
    session_id: str,
    client: str,
    entries: list[LedgerEntry],
    session_key: str | None = None,
    started_at: str | None = None,
    status: str = "active",
    final_summary: str | None = None,
) -> str:
    """Render the full handoff document for one session."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    goals = [e for e in entries if e.kind == "goal"]
    goal = current_goal(entries)
    turns = max((e.turn_index for e in entries), default=0)

    out: list[str] = [
        f"# Session Handoff — `{session_id}`",
        "",
        "> Continuously updated by Hippocampus on every `log_progress` call.",
        "> After a context compaction/summarization, or when resuming this session,",
        "> READ THIS FILE FIRST and re-anchor on the main goal below — then call",
        "> `get_progress()` for the live ledger before continuing.",
        "",
        f"- **status**: {status}",
        f"- **client**: `{client}`",
    ]
    if session_key:
        out.append(f"- **session_key**: `{session_key}`")
    if started_at:
        out.append(f"- **started**: {started_at} UTC")
    out.append(f"- **updated**: {now} UTC")
    out.append(f"- **turns**: {turns}")
    out.append("")

    out.append("## Main goal")
    out.append("")
    if goal is not None:
        out.extend(_entry_lines(goal))
        if goal.kind == "ask":
            out.append("  _(inferred from first ask — no explicit goal logged)_")
    else:
        out.append("_(no goal or ask logged yet)_")
    out.append("")

    if len(goals) > 1:
        out.append("### Goal history (oldest first)")
        out.append("")
        for g in goals:
            out.extend(_entry_lines(g))
        out.append("")

    last = entries[-1] if entries else None
    if last is not None:
        out.append("## Where we are now")
        out.append("")
        out.append(f"- last activity: [{_fmt_ts(last.created_at)}] **{last.kind}** — {last.content.strip()}")
        out.append("")

    def _section(title: str, kind: str, *, only_unresolved: bool = False) -> None:
        items = [e for e in entries if e.kind == kind]
        if only_unresolved:
            items = [e for e in items if not e.resolved]
        if not items:
            return
        out.append(f"## {title}")
        out.append("")
        for e in items:
            out.extend(_entry_lines(e))
        out.append("")

    _section("Done so far (oldest first)", "done")
    _section("Open blockers", "blocker", only_unresolved=True)
    _section("Decisions", "decision")
    _section("Next steps", "next")
    _section("Asks (oldest first)", "ask")
    _section("Notes", "note")

    if final_summary and final_summary.strip():
        out.append("## Final summary")
        out.append("")
        out.append(final_summary.strip())
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def write_handoff(
    *,
    session_id: str,
    client: str,
    entries: list[LedgerEntry],
    session_key: str | None = None,
    started_at: str | None = None,
    status: str = "active",
    final_summary: str | None = None,
) -> tuple[Path, bool]:
    """Render + write the handoff file. Returns (path, changed)."""
    path = handoff_path(session_id)
    body = render_handoff(
        session_id=session_id,
        client=client,
        entries=entries,
        session_key=session_key,
        started_at=started_at,
        status=status,
        final_summary=final_summary,
    )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.exists():
        path.chmod(0o600)
        if path.read_text(encoding="utf-8") == body:
            return path, False
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)
    return path, True
