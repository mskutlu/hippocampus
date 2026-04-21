"""Implementation of each MCP tool.

Tools are plain Python functions so the CLI and tests can call them without
spinning up the MCP transport. The `server.py` file wraps them for MCP.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from hippocampus import config
from hippocampus.clients.injector import (
    format_injection_block,
    format_working_block,
    upsert_block,
    upsert_working_block,
)
from hippocampus.clients.registry import CLIENTS, by_name
from hippocampus.dynamics import boost as boost_dyn
from hippocampus.dynamics import ranking
from hippocampus.storage import (
    associations,
    fragments as frag_store,
    ledger as ledger_store,
    sessions,
    feedback,
)
from hippocampus.sync import obsidian_mirror

# ULID alphabet: Crockford base32 — no I, L, O, U.
FRAGMENT_ID_RE = re.compile(r"frag_[0-9A-HJKMNP-TV-Z]{26}")


def _ensure_bootstrapped() -> None:
    """Idempotent initialisation: dirs, DB, mirror hooks."""
    config.ensure_dirs()
    from hippocampus.storage import db as sdb

    sdb.init_db()
    obsidian_mirror.bootstrap_hooks()


def _client_from_env() -> str:
    """Identify the calling AI client via env var (MCP clients set HIPPOCAMPUS_CLIENT)."""
    return os.environ.get("HIPPOCAMPUS_CLIENT", "unknown").strip().lower() or "unknown"


def _as_dict(frag) -> dict[str, Any]:
    return frag.to_dict()


# ---------------------------------------------------------------------------
# Long-term memory tools (V1)
# ---------------------------------------------------------------------------


def recall(
    query: str,
    limit: int = 5,
    min_confidence: float = 0.0,
    context_tag: str | None = None,
) -> dict[str, Any]:
    """Hybrid FTS + semantic search. Every returned hit is boosted.

    Score blend:
        final = fts_rank_norm * (1 - semantic_weight) + cosine * semantic_weight

    `semantic_weight` is a setting (default 0.5). When embeddings aren't
    available, we fall back to pure FTS. When FTS returns nothing, we fall
    back to pure semantic.
    """
    _ensure_bootstrapped()

    pool_size = max(limit * 4, limit + 3)

    # --- FTS candidates -----------------------------------------------------
    fts_hits = frag_store.search_fts(
        query=query, limit=pool_size, min_confidence=min_confidence
    )
    fts_scores: dict[str, float] = {}
    fts_frags: dict[str, Any] = {}
    for rank_idx, f in enumerate(fts_hits):
        # Rank-based normalised score in (0, 1]. Position 0 -> 1.0, fades.
        fts_scores[f.id] = 1.0 / (1.0 + rank_idx)
        fts_frags[f.id] = f

    # --- Semantic candidates ------------------------------------------------
    semantic_scores: dict[str, float] = {}
    semantic_available = False
    try:
        from hippocampus.embeddings import search as semantic_search  # lazy
        sem_hits = semantic_search.semantic_topk(query, k=pool_size)
        for fid, score in sem_hits:
            semantic_scores[fid] = max(0.0, float(score))
        semantic_available = len(sem_hits) > 0
    except Exception:
        semantic_available = False

    if not fts_scores and not semantic_scores:
        return {
            "query": query,
            "count": 0,
            "fragments": [],
            "semantic_available": semantic_available,
            "semantic_weight": 0.0,
        }

    # --- Blend --------------------------------------------------------------
    w_sem = float(config.get_setting("semantic_weight") or 0.5)
    if not semantic_available:
        w_sem = 0.0
    if not fts_scores:
        w_sem = 1.0
    w_fts = 1.0 - w_sem

    all_ids = set(fts_scores) | set(semantic_scores)
    ranked: list[tuple[str, float]] = []
    for fid in all_ids:
        combined = (
            fts_scores.get(fid, 0.0) * w_fts
            + semantic_scores.get(fid, 0.0) * w_sem
        )
        ranked.append((fid, combined))
    ranked.sort(key=lambda t: -t[1])
    top = ranked[:limit]

    # Hydrate fragments — prefer those already in the FTS pool
    hit_frags = []
    for fid, _ in top:
        f = fts_frags.get(fid) or frag_store.get(fid)
        if f is not None and f.confidence >= min_confidence:
            hit_frags.append(f)
    if not hit_frags:
        return {
            "query": query,
            "count": 0,
            "fragments": [],
            "semantic_available": semantic_available,
            "semantic_weight": round(w_sem, 2),
        }

    # Boost all hits (biology) + associations
    client = _client_from_env()
    session_id = sessions.current_session_id(client)
    hit_ids = [f.id for f in hit_frags]
    boosted = boost_dyn.boost_many(
        hit_ids, context_tag=context_tag, session_id=session_id, client=client
    )

    fragments_out: list[dict[str, Any]] = []
    for f in boosted:
        fragments_out.append(
            {
                "id": f.id,
                "summary": f.summary,
                "content": f.content,
                "confidence": round(f.confidence, 6),
                "accessed": f.accessed,
                "tags": f.tags,
                "pinned": f.pinned,
                "associated_with": f.associated_with,
                "scores": {
                    "fts": round(fts_scores.get(f.id, 0.0), 4),
                    "semantic": round(semantic_scores.get(f.id, 0.0), 4),
                },
            }
        )

    return {
        "query": query,
        "count": len(fragments_out),
        "semantic_available": semantic_available,
        "semantic_weight": round(w_sem, 2),
        "fragments": fragments_out,
    }


def remember(
    content: str,
    summary: str | None = None,
    tags: Sequence[str] | None = None,
    source_type: str = "manual",
    source_ref: str | None = None,
    pinned: bool = False,
) -> dict[str, Any]:
    _ensure_bootstrapped()

    content = (content or "").strip()
    if not content:
        raise ValueError("content is required")

    resolved_summary = (summary or "").strip()
    if not resolved_summary:
        snippet = content.split("\n", 1)[0][:160]
        for sep in (". ", "? ", "! "):
            if sep in snippet:
                resolved_summary = snippet.split(sep, 1)[0].strip() + sep.strip()
                break
        if not resolved_summary:
            resolved_summary = snippet

    frag = frag_store.create(
        content=content,
        summary=resolved_summary,
        tags=list(tags or []),
        source_type=source_type,
        source_ref=source_ref,
        pinned=pinned,
    )
    # Try to embed synchronously; failure is non-fatal (fragment already
    # stored, can be re-embedded later via `hippo reindex`).
    try:
        from hippocampus.embeddings import search as semantic_search
        semantic_search.upsert_for_fragment(frag.id)
    except Exception:
        pass
    return {"stored": True, "fragment": _as_dict(frag)}


def forget(fragment_id: str, reason: str | None = None) -> dict[str, Any]:
    _ensure_bootstrapped()
    updated = boost_dyn.apply_negative_feedback(fragment_id, reason=reason)
    if updated is None:
        return {"found": False, "fragment_id": fragment_id}
    return {"found": True, "fragment": _as_dict(updated)}


def pin(fragment_id: str) -> dict[str, Any]:
    _ensure_bootstrapped()
    updated = frag_store.update_fields(fragment_id, pinned=True)
    if updated is None:
        return {"found": False, "fragment_id": fragment_id}
    feedback.log(fragment_id, "pin")
    return {"found": True, "fragment": _as_dict(updated)}


def unpin(fragment_id: str) -> dict[str, Any]:
    _ensure_bootstrapped()
    updated = frag_store.update_fields(fragment_id, pinned=False)
    if updated is None:
        return {"found": False, "fragment_id": fragment_id}
    feedback.log(fragment_id, "unpin")
    return {"found": True, "fragment": _as_dict(updated)}


def get_fragment(fragment_id: str, boost_on_read: bool = True) -> dict[str, Any]:
    _ensure_bootstrapped()
    if boost_on_read:
        client = _client_from_env()
        session_id = sessions.current_session_id(client)
        updated = boost_dyn.boost(fragment_id, session_id=session_id, client=client)
        if updated is None:
            return {"found": False, "fragment_id": fragment_id}
        return {"found": True, "fragment": _as_dict(updated)}

    frag = frag_store.get(fragment_id)
    if frag is None:
        return {"found": False, "fragment_id": fragment_id}
    return {"found": True, "fragment": _as_dict(frag)}


def list_fragments(
    tag: str | None = None, min_confidence: float = 0.0, limit: int = 20
) -> dict[str, Any]:
    _ensure_bootstrapped()
    items = (
        frag_store.list_by_tag(tag, limit=limit)
        if tag
        else frag_store.list_all(min_confidence=min_confidence, limit=limit)
    )
    return {"count": len(items), "fragments": [_as_dict(f) for f in items]}


def top_fragments(limit: int | None = None) -> dict[str, Any]:
    _ensure_bootstrapped()
    items = ranking.top_n(limit=limit)
    return {"count": len(items), "fragments": [_as_dict(f) for f in items]}


def get_stats() -> dict[str, Any]:
    _ensure_bootstrapped()
    total = frag_store.count()
    all_frags = frag_store.list_all(min_confidence=0.0, limit=10_000)
    pinned = sum(1 for f in all_frags if f.pinned)
    avg_confidence = (
        sum(f.confidence for f in all_frags) / len(all_frags) if all_frags else 0.0
    )
    recent_feedback = feedback.recent(limit=10)
    return {
        "total_fragments": total,
        "pinned_fragments": pinned,
        "average_confidence": round(avg_confidence, 6),
        "recent_feedback": recent_feedback,
        "archive_threshold": config.ARCHIVE_THRESHOLD,
        "boost_delta": config.BOOST_DELTA,
        "decay_delta": config.DECAY_DELTA,
        "feedback_delta": config.FEEDBACK_DELTA,
        "current_time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# Working memory tools (V0.2)
# ---------------------------------------------------------------------------


def _refresh_working_block(client: str) -> None:
    """Regenerate the WORKING block for one client's rules file.

    Called after every `log_progress` so the block reflects the new state on
    the very next AI turn. Idempotent (hash-checked) so a no-op logs nothing
    to disk.
    """
    spec = by_name(client)
    if spec is None:
        return
    try:
        sid = sessions.current_session_id(client, open_if_missing=False)
        entries = ledger_store.current_entries(sid)
        row = _session_row(sid)
        started_at = row["started_at"] if row else None
    except (RuntimeError, Exception):
        sid = None
        entries = []
        started_at = None

    block = format_working_block(
        session_id=sid,
        client=client,
        started_at=started_at,
        entries=entries,
    )
    upsert_working_block(
        spec.rules_path, block,
        create_if_missing=True,
        header_when_creating=spec.creation_header,
    )


def _session_row(session_id: str) -> dict | None:
    from hippocampus.storage.db import get_ro_conn

    with get_ro_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def log_progress(
    kind: str,
    content: str,
    details: str | None = None,
    client: str | None = None,
) -> dict[str, Any]:
    """Append a working-memory entry and refresh the WORKING block.

    Side-effect: any `frag_...` id mentioned in `content` or `details` is
    boosted as if recalled, with context_tag=`log_progress:<kind>` so the
    AI's activity implicitly strengthens the fragments it's working with.
    """
    _ensure_bootstrapped()

    client_name = (client or _client_from_env()).strip().lower()
    if not client_name or client_name == "unknown":
        client_name = "cli"
    session_id = sessions.current_session_id(client_name, open_if_missing=True)

    entry = ledger_store.log_entry(
        session_id=session_id,
        client=client_name,
        kind=kind,
        content=content,
        details=details,
    )
    if entry is None:
        return {
            "logged": False,
            "reason": "duplicate_within_dedup_window",
            "session_id": session_id,
        }

    # Auto-tag / boost fragments referenced in the entry.
    referenced_ids: list[str] = []
    haystack = " ".join(filter(None, [content, details]))
    for match in FRAGMENT_ID_RE.findall(haystack):
        frag = frag_store.get(match)
        if frag is None:
            continue
        boost_dyn.boost(
            match,
            context_tag=f"log_progress:{kind}",
            session_id=session_id,
            client=client_name,
        )
        referenced_ids.append(match)

    _refresh_working_block(client_name)
    return {
        "logged": True,
        "entry": entry.to_dict(),
        "boosted_fragments": referenced_ids,
    }


def undo_last_entry(client: str | None = None) -> dict[str, Any]:
    """Pop the most recent ledger entry for the client's current session.

    Refuses if the entry is older than 5 minutes — use `end_progress` for
    older corrections.
    """
    _ensure_bootstrapped()
    client_name = (client or _client_from_env()).strip().lower() or "cli"
    try:
        sid = sessions.current_session_id(client_name, open_if_missing=False)
    except RuntimeError:
        return {"undone": False, "reason": "no_active_session", "client": client_name}

    peek = ledger_store.current_entries(sid)
    if not peek:
        return {"undone": False, "reason": "empty_ledger", "session_id": sid}

    last = peek[-1]
    try:
        created_at = datetime.fromisoformat(last.created_at.replace("Z", "+00:00"))
    except ValueError:
        created_at = datetime.now(timezone.utc)
    age = datetime.now(timezone.utc) - created_at
    if age > timedelta(minutes=5):
        return {
            "undone": False,
            "reason": "entry_too_old",
            "age_seconds": age.total_seconds(),
            "last_entry": last.to_dict(),
        }

    deleted = ledger_store.delete_last_entry(sid)
    _refresh_working_block(client_name)
    return {"undone": True, "entry": deleted.to_dict() if deleted else None}


def get_progress(client: str | None = None, full: bool = False) -> dict[str, Any]:
    _ensure_bootstrapped()
    client_name = (client or _client_from_env()).strip().lower() or "unknown"
    try:
        sid = sessions.current_session_id(client_name, open_if_missing=False)
    except RuntimeError:
        return {"client": client_name, "session_id": None, "entries": []}

    entries = ledger_store.current_entries(sid)
    payload = [e.to_dict() for e in entries]
    if not full:
        payload = payload[-50:]  # last 50 is usually plenty
    return {"client": client_name, "session_id": sid, "count": len(entries), "entries": payload}


def end_progress(
    distill_to_fragment: bool = False,
    summary: str | None = None,
    tags: Sequence[str] | None = None,
    client: str | None = None,
) -> dict[str, Any]:
    """Close the current session for `client` and optionally distill a fragment."""
    _ensure_bootstrapped()
    client_name = (client or _client_from_env()).strip().lower() or "unknown"

    try:
        sid = sessions.current_session_id(client_name, open_if_missing=False)
    except RuntimeError:
        return {"rotated": False, "reason": "no_active_session", "client": client_name}

    entries = ledger_store.current_entries(sid)
    stored_fragment: dict | None = None
    if distill_to_fragment:
        if not entries:
            return {"rotated": False, "reason": "no_entries_to_distill", "session_id": sid}
        content = _render_ledger_as_fragment(entries, explicit_summary=summary)
        resolved_summary = (summary or _derive_summary(entries)).strip() or "session summary"
        frag = frag_store.create(
            content=content,
            summary=resolved_summary,
            tags=list(tags or []) + ["session-summary", client_name],
            source_type="session-summary",
            source_ref=sid,
        )
        stored_fragment = _as_dict(frag)

    new_sid = sessions.rotate(client_name)
    _refresh_working_block(client_name)

    return {
        "rotated": True,
        "previous_session_id": sid,
        "new_session_id": new_sid,
        "client": client_name,
        "distilled_fragment": stored_fragment,
    }


def _derive_summary(entries: list[ledger_store.LedgerEntry]) -> str:
    """Build a one-line summary when the caller didn't provide one."""
    goal = next((e for e in entries if e.kind == "goal"), None)
    if goal:
        return f"Session summary: {goal.content[:120]}"
    first_ask = next((e for e in entries if e.kind == "ask"), None)
    if first_ask:
        return f"Session summary: {first_ask.content[:120]}"
    return "Session summary"


def _render_ledger_as_fragment(entries: list[ledger_store.LedgerEntry], *, explicit_summary: str | None) -> str:
    """Build the full fragment body from a ledger."""
    lines: list[str] = []
    if explicit_summary:
        lines.append(explicit_summary)
        lines.append("")
    by_kind: dict[str, list[str]] = {}
    for e in entries:
        by_kind.setdefault(e.kind, []).append(f"- {e.content}")
    for kind in ("goal", "decision", "done", "blocker", "next", "ask", "note"):
        items = by_kind.get(kind, [])
        if not items:
            continue
        lines.append(f"**{kind.title()}**")
        lines.extend(items)
        lines.append("")
    return "\n".join(lines).strip()


def auto_end_idle_sessions() -> dict[str, Any]:
    """Close any open session whose last activity exceeds `auto_end_idle_minutes`.

    Called from the decay cycle, so it runs at most once per hour (and only
    if the user has set `auto_end_idle_minutes`).
    """
    _ensure_bootstrapped()
    minutes = config.get_setting("auto_end_idle_minutes")
    if not minutes:  # None or 0 disables
        return {"ended": 0, "reason": "disabled"}
    minutes_int = int(minutes)
    ended: list[dict[str, Any]] = []
    for sid, client in sessions.idle_sessions(minutes_int):
        sessions.close_session(sid)
        sessions.open_session(client)
        _refresh_working_block(client)
        ended.append({"session_id": sid, "client": client})
    return {"ended": len(ended), "minutes": minutes_int, "sessions": ended}


def _derive_summary(entries: list[ledger_store.LedgerEntry]) -> str:
    """Build a one-line summary when the caller didn't provide one."""
    goal = next((e for e in entries if e.kind == "goal"), None)
    if goal:
        return f"Session summary: {goal.content[:120]}"
    first_ask = next((e for e in entries if e.kind == "ask"), None)
    if first_ask:
        return f"Session summary: {first_ask.content[:120]}"
    return "Session summary"


def _render_ledger_as_fragment(entries: list[ledger_store.LedgerEntry], *, explicit_summary: str | None) -> str:
    """Build the full fragment body from a ledger."""
    lines: list[str] = []
    if explicit_summary:
        lines.append(explicit_summary)
        lines.append("")
    by_kind: dict[str, list[str]] = {}
    for e in entries:
        by_kind.setdefault(e.kind, []).append(f"- {e.content}")
    for kind in ("goal", "decision", "done", "blocker", "next", "ask", "note"):
        items = by_kind.get(kind, [])
        if not items:
            continue
        lines.append(f"**{kind.title()}**")
        lines.extend(items)
        lines.append("")
    return "\n".join(lines).strip()

