"""Render the live working ledger + top recall hits as plain markdown.

Used by the lifecycle-hook scripts (SessionStart, UserPromptSubmit,
PostCompaction) to inject a fresh snapshot of memory into the model's
context on every relevant event — independent of whatever the rules file
currently contains. This is what makes the WORKING block compaction-safe:
the rules-file snapshot taken at session start may be stale, but the
hook payload always reflects the current ledger.

Output is plain markdown (no HTML marker pair). The marker pair is only
used as a landmark inside rules files; it is meaningless inside
`hookSpecificOutput.additionalContext` and just wastes tokens.
"""

from __future__ import annotations

from datetime import datetime, timezone

from hippocampus import config
from hippocampus.storage import ledger as ledger_store, sessions


def _fmt_time(iso_utc: str | None) -> str:
    if not iso_utc:
        return "--:--"
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%H:%M")
    except ValueError:
        return "--:--"


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _working_section(client: str, *, content_max: int = 220) -> list[str]:
    """Render the live ledger as a compact markdown block. Empty list if none."""
    try:
        sid = sessions.current_session_id(client, open_if_missing=False)
    except (RuntimeError, Exception):
        return []
    entries = ledger_store.current_entries(sid)
    if not entries:
        return [
            "## Current working session",
            f"_Session `{sid}` open for `{client}` — no entries yet._",
            "",
        ]

    grouped = ledger_store.grouped_for_render(
        entries,
        max_asks=5,
        max_dones=5,
        max_next=3,
        max_notes=3,
    )

    started_at = None
    try:
        from hippocampus.storage.db import get_ro_conn

        with get_ro_conn() as conn:
            row = conn.execute(
                "SELECT started_at FROM sessions WHERE id = ?", (sid,)
            ).fetchone()
            if row:
                started_at = row["started_at"]
    except Exception:
        started_at = None

    out: list[str] = [
        "## Current working session",
        f"`{sid}` · client `{client}` · started {_fmt_time(started_at)} UTC · turn {grouped['turn_count']}",
        "",
    ]
    if grouped["goal"]:
        g = grouped["goal"]
        out.append(f"**Goal** — [{_fmt_time(g.created_at)}] {_short(g.content, content_max)}")
        out.append("")

    def _emit(title: str, items, glyph: str = "-") -> None:
        if not items:
            return
        out.append(f"**{title}**")
        for e in items:
            out.append(f"{glyph} [{_fmt_time(e.created_at)}] {_short(e.content, content_max)}")
        out.append("")

    _emit("Asks (latest)", grouped["asks"])
    _emit("Done", grouped["dones"])
    _emit("Blockers (open)", grouped["blockers"], glyph="✗")
    _emit("Decisions", grouped["decisions"])
    _emit("Next", grouped["nexts"])
    _emit("Notes", grouped["notes"])
    return out


def _fragment_section(
    query: str | None,
    *,
    limit: int,
    extra_query_streams: list[str] | None = None,
) -> list[str]:
    """Render top-N long-term fragments (query-driven if query, else ranked).

    v1.6.0 (C1 — predictive recall): `extra_query_streams` is an optional list
    of strings (typically the current session's last ask + last 3 dones) that
    are also recalled and merged into the result set. Each stream contributes
    1–2 hits if the explicit `query` doesn't already saturate the slot budget.
    """
    try:
        from hippocampus.mcp import tools as T  # lazy import — keeps the
        # hook fast for users with semantic disabled
    except Exception:
        return []

    fragments_by_id: dict[str, dict] = {}

    def _add_hits(hits: list[dict]) -> None:
        for h in hits:
            fid = h.get("id")
            if fid and fid not in fragments_by_id:
                fragments_by_id[fid] = h

    if query and query.strip():
        try:
            recall_out = T.recall(query=query.strip(), limit=limit)
            _add_hits(recall_out.get("fragments") or [])
        except Exception:
            pass

    # Predictive recall — turn ledger context into additional query streams.
    if extra_query_streams and len(fragments_by_id) < limit:
        for stream in extra_query_streams:
            if len(fragments_by_id) >= limit:
                break
            if not stream or not stream.strip():
                continue
            try:
                stream_out = T.recall(query=stream.strip(), limit=2)
            except Exception:
                continue
            _add_hits(stream_out.get("fragments") or [])

    if not fragments_by_id:
        try:
            top_out = T.top_fragments(limit=limit)
            _add_hits(top_out.get("fragments") or [])
        except Exception:
            pass

    if not fragments_by_id:
        return []

    # Preserve insertion order (explicit query first, then ledger streams,
    # then top-N) and respect the limit.
    fragments = list(fragments_by_id.values())[:limit]

    header = (
        "## Top memories matching the latest prompt"
        if query and query.strip()
        else "## Top long-term memories right now"
    )
    out: list[str] = [header, ""]
    for f in fragments:
        conf = f"{f.get('confidence', 0.0):.2f}"
        pin = " 📌" if f.get("pinned") else ""
        tags = f.get("tags") or []
        tagstr = f" [{', '.join(tags)}]" if tags else ""
        summary = _short(f.get("summary") or f.get("content") or "", 200)
        out.append(f"- `{f.get('id')}` (conf={conf}){pin}{tagstr} — {summary}")
    out.append("")
    return out


def _ledger_query_streams(client: str, *, max_streams: int = 4) -> list[str]:
    """Pull the most recent ask + last 3 dones from the live ledger as extra
    query streams. Used by `render_context` for predictive recall (v1.6.0 C1).
    """
    try:
        sid = sessions.current_session_id(client, open_if_missing=False)
    except (RuntimeError, Exception):
        return []
    entries = ledger_store.current_entries(sid)
    if not entries:
        return []
    streams: list[str] = []
    # Most recent ask first — it's the closest signal to user intent.
    for e in reversed(entries):
        if e.kind == "ask" and e.content:
            streams.append(e.content)
            break
    # Then most recent 3 dones — they reveal what the AI was just doing.
    done_count = 0
    for e in reversed(entries):
        if done_count >= 3:
            break
        if e.kind == "done" and e.content:
            streams.append(e.content)
            done_count += 1
    return streams[:max_streams]


def render_context(
    *,
    client: str,
    query: str | None = None,
    include_working: bool = True,
    include_fragments: bool = True,
    fragment_limit: int = 5,
    char_budget: int = 3500,
    event_name: str | None = None,
    extra_query_streams: list[str] | None = None,
) -> str:
    """Plain-markdown payload for hookSpecificOutput.additionalContext.

    The payload always starts with a one-line provenance header so the AI
    can tell where the injection came from. Sections that would exceed
    `char_budget` are dropped (working block has priority over fragments).

    v1.6.0: if `extra_query_streams` is None, the ledger's recent ask + dones
    are used automatically (C1 predictive recall). Pass `[]` to disable.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    suffix = f" (event={event_name})" if event_name else ""
    header = [
        f"**[Hippocampus] live memory snapshot{suffix} — {now}**",
        "",
        "> The rules file may carry a stale WORKING block; this payload is the source of truth.",
        "",
    ]

    # Default predictive streams = the live ledger's recent ask + dones.
    if extra_query_streams is None:
        extra_query_streams = _ledger_query_streams(client)

    sections: list[list[str]] = []
    if include_working:
        sec = _working_section(client)
        if sec:
            sections.append(sec)
    if include_fragments:
        sec = _fragment_section(query, limit=fragment_limit, extra_query_streams=extra_query_streams)
        if sec:
            sections.append(sec)

    # Assemble respecting budget. Header always wins.
    out_lines: list[str] = list(header)
    used = sum(len(s) + 1 for s in out_lines)
    for sec in sections:
        sec_text = "\n".join(sec) + "\n"
        if used + len(sec_text) > char_budget:
            remaining = char_budget - used
            if remaining > 80:  # only bother truncating if we have room for a useful prefix
                out_lines.append(sec_text[: remaining - 1].rstrip())
                out_lines.append("…(truncated)")
            break
        out_lines.extend(sec)
        used += len(sec_text)

    if len(out_lines) == len(header):
        out_lines.append("_(no working session and no fragments stored)_")
    return "\n".join(out_lines).rstrip() + "\n"
