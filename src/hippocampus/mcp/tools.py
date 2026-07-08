"""Implementation of each MCP tool.

Tools are plain Python functions so the CLI and tests can call them without
spinning up the MCP transport. The `server.py` file wraps them for MCP.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    transcript as transcript_store,
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


def _client_name(client: str | None = None) -> str:
    name = (client or _client_from_env()).strip().lower()
    if not name or name == "unknown":
        return "cli"
    return name


def _as_dict(frag) -> dict[str, Any]:
    return frag.to_dict()


# ---------------------------------------------------------------------------
# Long-term memory tools (V1)
# ---------------------------------------------------------------------------


_RRF_K = 60  # standard RRF constant


def _rrf(rank: int | None) -> float:
    """Reciprocal-rank contribution for a 1-based rank; 0 when absent."""
    return 1.0 / (_RRF_K + rank) if rank else 0.0


def recall(
    query: str,
    limit: int = 5,
    min_confidence: float = 0.0,
    context_tag: str | None = None,
) -> dict[str, Any]:
    """Hybrid FTS + semantic search. Every returned hit is boosted.

    Candidates are fused with weighted Reciprocal Rank Fusion (RRF):
        final = (1 - semantic_weight) / (60 + fts_rank)
              + semantic_weight / (60 + semantic_rank)

    Rank-based fusion sidesteps the scale mismatch between FTS rank scores
    and raw cosine values. `semantic_weight` is a setting (default 0.5).
    When embeddings aren't available, we fall back to pure FTS. When FTS
    returns nothing, we fall back to pure semantic.
    """
    _ensure_bootstrapped()

    pool_size = max(limit * 4, limit + 3)

    # --- FTS candidates -----------------------------------------------------
    # FTS5 has its own syntax (column:value, hyphens, quotes, parentheses, etc).
    # Free-text user queries can trip the parser; degrade to semantic-only on error.
    try:
        fts_hits = frag_store.search_fts(
            query=query, limit=pool_size, min_confidence=min_confidence
        )
    except Exception:
        # FTS parse error — fall back to semantic-only. Try a sanitised retry
        # first so we still get FTS hits for the common single-word case.
        import re as _re
        sanitised = _re.sub(r"[^\w\s]", " ", query).strip()
        try:
            fts_hits = frag_store.search_fts(
                query=sanitised, limit=pool_size, min_confidence=min_confidence
            ) if sanitised else []
        except Exception:
            fts_hits = []
    fts_ranks: dict[str, int] = {}
    fts_frags: dict[str, Any] = {}
    for rank_idx, f in enumerate(fts_hits):
        fts_ranks[f.id] = rank_idx + 1
        fts_frags[f.id] = f

    # --- Semantic candidates ------------------------------------------------
    semantic_ranks: dict[str, int] = {}
    semantic_scores: dict[str, float] = {}  # raw cosine, for diagnostics only
    semantic_available = False
    try:
        from hippocampus.embeddings import search as semantic_search  # lazy
        sem_hits = semantic_search.semantic_topk(query, k=pool_size)
        for rank_idx, (fid, score) in enumerate(sem_hits):
            semantic_ranks[fid] = rank_idx + 1
            semantic_scores[fid] = max(0.0, float(score))
        semantic_available = len(sem_hits) > 0
    except Exception:
        semantic_available = False

    if not fts_ranks and not semantic_ranks:
        return {
            "query": query,
            "count": 0,
            "fragments": [],
            "semantic_available": semantic_available,
            "semantic_weight": 0.0,
        }

    # --- Fuse (weighted RRF) --------------------------------------------------
    w_sem = float(config.get_setting("semantic_weight") or 0.5)
    if not semantic_available:
        w_sem = 0.0
    if not fts_ranks:
        w_sem = 1.0
    w_fts = 1.0 - w_sem

    all_ids = set(fts_ranks) | set(semantic_ranks)
    ranked: list[tuple[str, float]] = []
    for fid in all_ids:
        combined = (
            _rrf(fts_ranks.get(fid)) * w_fts
            + _rrf(semantic_ranks.get(fid)) * w_sem
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
    client = _client_name()
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
                    "fts_rank": fts_ranks.get(f.id),
                    "semantic_rank": semantic_ranks.get(f.id),
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
        client = _client_name()
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


def _refresh_working_block(client: str, session_key: str | None = None) -> None:
    """Regenerate the WORKING block for one client's rules file.

    Called after every `log_progress` so the block reflects the new state on
    the very next AI turn. Idempotent (hash-checked) so a no-op logs nothing
    to disk.
    """
    spec = by_name(client)
    if spec is None:
        return
    try:
        sid = sessions.current_session_id(client, session_key=session_key, open_if_missing=False)
        entries = ledger_store.current_entries(sid)
        row = _session_row(sid)
        started_at = row["started_at"] if row else None
    except (RuntimeError, Exception):
        sid = None
        entries = []
        started_at = None

    handoff_path: str | None = None
    if sid is not None:
        from hippocampus import handoff as handoff_mod

        candidate = handoff_mod.handoff_path(sid)
        if candidate.exists():
            handoff_path = str(candidate)

    block = format_working_block(
        session_id=sid,
        client=client,
        started_at=started_at,
        entries=entries,
        handoff_path=handoff_path,
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


def _write_session_handoff(
    session_id: str,
    client: str,
    entries: list | None = None,
    *,
    status: str = "active",
    final_summary: str | None = None,
) -> tuple[str | None, str | None]:
    """Write the session's handoff file (best-effort).

    Returns (handoff_path, current_goal_content); (None, goal) when the
    feature is disabled or the write fails.
    """
    from hippocampus import handoff as handoff_mod

    if entries is None:
        try:
            entries = ledger_store.current_entries(session_id)
        except Exception:
            entries = []
    goal_entry = handoff_mod.current_goal(entries)
    goal = goal_entry.content if goal_entry else None
    if not handoff_mod.enabled():
        return None, goal
    try:
        row = _session_row(session_id) or {}
        path, _ = handoff_mod.write_handoff(
            session_id=session_id,
            client=client,
            entries=entries,
            session_key=row.get("session_key"),
            started_at=row.get("started_at"),
            status=status,
            final_summary=final_summary,
        )
        return str(path), goal
    except Exception:
        return None, goal


def _current_context(client_name: str) -> tuple[str, str]:
    session_key = sessions.derive_session_key()
    session_id = sessions.current_session_id(client_name, session_key=session_key, open_if_missing=True)
    return session_id, session_key


def _log_progress_transcript(
    *,
    session_id: str,
    session_key: str,
    client_name: str,
    kind: str,
    content: str,
    details: str | None,
) -> None:
    if kind == "ask" and os.environ.get("HIPPOCAMPUS_TRANSCRIPT_PROMPT_LOGGED"):
        return
    role = "user" if kind == "ask" else ("reasoning_summary" if kind == "decision" else "assistant_summary")
    full_content = content if not details else f"{content}\n\n{details}"
    try:
        transcript_store.log_entry(
            session_id=session_id,
            client=client_name,
            session_key=session_key,
            role=role,
            content=full_content,
            source_event=f"log_progress:{kind}",
            metadata={"ledger_kind": kind},
        )
    except Exception:
        pass


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

    client_name = _client_name(client)
    session_id, session_key = _current_context(client_name)

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

    _log_progress_transcript(
        session_id=session_id,
        session_key=session_key,
        client_name=client_name,
        kind=kind,
        content=content,
        details=details,
    )

    # v1.6.0 — negation inference (B4). When the user pushes back at the start
    # of an ask, demote the most recent fragment we boosted in this session.
    demoted_id: str | None = None
    if kind == "ask" and content:
        try:
            from hippocampus.dynamics import negation as negation_dyn
            demoted_id = negation_dyn.infer_and_forget(content, session_id=session_id)
        except Exception:
            demoted_id = None

    # Auto-tag / boost fragments referenced in the entry by explicit `frag_…` id.
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

    # v1.6.0 — also boost the top-K semantically-matched fragments (A5 + C2).
    # Means every log_progress reinforces the knowledge graph, not just entries
    # the AI bothered to cite by id. Cluster propagation pushes a smaller boost
    # to first-degree neighbors so co-accessed knowledge stays warm.
    auto_boosted: list[str] = []
    try:
        k = int(config.get_setting("log_progress_recall_boost_k") or 0)
        min_score = float(config.get_setting("log_progress_recall_min_score") or 0.0)
        if k > 0 and haystack.strip():
            search_res = recall(query=haystack, limit=k * 2, context_tag=None)
            hits = (search_res or {}).get("fragments") or []
            keepers: list[str] = []
            for h in hits:
                scores = h.get("scores") or {}
                sem = float(scores.get("semantic") or 0.0)
                if sem < min_score:
                    continue
                if h["id"] in referenced_ids:
                    continue  # already boosted by explicit-id path
                keepers.append(h["id"])
                if len(keepers) >= k:
                    break
            if keepers:
                boost_dyn.boost_many(
                    keepers,
                    context_tag=f"log_progress_auto:{kind}",
                    session_id=session_id,
                    client=client_name,
                    cluster_propagate=True,
                )
                auto_boosted = keepers
    except Exception:
        # Best-effort enrichment; never break log_progress.
        auto_boosted = []

    # Handoff first so the fresh working block can advertise its path.
    handoff_path, goal = _write_session_handoff(session_id, client_name)
    _refresh_working_block(client_name)
    # `goal` is echoed on every call so the authoritative goal re-enters the
    # model's context via the tool-result channel — compaction-proof even for
    # clients with no lifecycle hooks.
    return {
        "logged": True,
        "entry": entry.to_dict(),
        "goal": goal,
        "handoff_path": handoff_path,
        "boosted_fragments": referenced_ids,
        "auto_boosted_fragments": auto_boosted,
        "demoted_fragment_id": demoted_id,
    }


def auto_remember(prompt: str, client: str | None = None) -> dict[str, Any]:
    """V9 G5 — scan a user prompt for autoremember triggers and persist if found.

    Returns {"remembered": bool, "fragment": <dict|None>, "trigger": <str|None>}.
    Designed to be called by the UserPromptSubmit hook on every turn.
    """
    _ensure_bootstrapped()
    client_name = _client_name(client)

    from hippocampus.dynamics import autoremember as auto_dyn

    rule = auto_dyn.detect(prompt or "")
    if rule is None:
        return {"remembered": False, "fragment": None, "trigger": None}

    frag = auto_dyn.auto_remember_from_prompt(prompt, client=client_name)
    if frag is None:
        return {"remembered": False, "fragment": None, "trigger": rule.trigger, "reason": "duplicate_or_disabled"}
    return {"remembered": True, "fragment": frag, "trigger": rule.trigger}


def undo_last_entry(client: str | None = None) -> dict[str, Any]:
    """Pop the most recent ledger entry for the client's current session.

    Refuses if the entry is older than 5 minutes — use `end_progress` for
    older corrections.
    """
    _ensure_bootstrapped()
    client_name = _client_name(client)
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
    _write_session_handoff(sid, client_name)
    return {"undone": True, "entry": deleted.to_dict() if deleted else None}


def get_progress(client: str | None = None, full: bool = False) -> dict[str, Any]:
    _ensure_bootstrapped()
    client_name = _client_name(client)
    try:
        sid = sessions.current_session_id(client_name, open_if_missing=False)
    except RuntimeError:
        return {"client": client_name, "session_id": None, "goal": None, "entries": []}

    entries = ledger_store.current_entries(sid)
    from hippocampus import handoff as handoff_mod

    goal_entry = handoff_mod.current_goal(entries)
    handoff_file = handoff_mod.handoff_path(sid)
    payload = [e.to_dict() for e in entries]
    if not full:
        payload = payload[-50:]  # last 50 is usually plenty
    return {
        "client": client_name,
        "session_id": sid,
        "goal": goal_entry.content if goal_entry else None,
        "handoff_path": str(handoff_file) if handoff_file.exists() else None,
        "count": len(entries),
        "entries": payload,
    }


def get_handoff(client: str | None = None) -> dict[str, Any]:
    """Return the handoff document for the current session.

    When no session is active for this client+session_key, falls back to the
    most recent session's handoff — that is the resume path: a fresh session
    (e.g. after a crash, restart, or compaction gone wrong) can call this to
    recover the goal and the full task state.
    """
    _ensure_bootstrapped()
    from hippocampus import handoff as handoff_mod

    client_name = _client_name(client)
    session_key = sessions.derive_session_key()
    resumed = False
    try:
        sid = sessions.current_session_id(client_name, session_key=session_key, open_if_missing=False)
    except RuntimeError:
        sid = None

    entries = ledger_store.current_entries(sid) if sid else []
    # A fresh/empty session (e.g. right after end_progress rotated, or after a
    # restart) has nothing to hand off — fall back to the most recent prior
    # session for this client+workspace. That is the resume path.
    if not entries and (sid is None or not handoff_mod.handoff_path(sid).exists()):
        from hippocampus.storage.db import get_ro_conn

        with get_ro_conn() as conn:
            row = conn.execute(
                """
                SELECT id FROM sessions
                WHERE client = ? AND session_key = ? AND id IS NOT ?
                ORDER BY started_at DESC LIMIT 1
                """,
                (client_name, session_key, sid),
            ).fetchone()
        if row:
            sid = row["id"]
            entries = ledger_store.current_entries(sid)
            resumed = True

    if sid is None:
        return {"client": client_name, "session_id": None, "exists": False, "content": None}
    goal_entry = handoff_mod.current_goal(entries)
    path = handoff_mod.handoff_path(sid)
    if not path.exists() and entries:
        # Sessions predating the handoff feature: materialize on demand.
        path_str, _ = _write_session_handoff(sid, client_name, entries)
        path = Path(path_str) if path_str else path

    return {
        "client": client_name,
        "session_id": sid,
        "resumed_from_previous_session": resumed,
        "goal": goal_entry.content if goal_entry else None,
        "path": str(path),
        "exists": path.exists(),
        "content": path.read_text(encoding="utf-8") if path.exists() else None,
    }


def log_transcript(
    role: str,
    content: str,
    source_event: str | None = None,
    metadata: dict[str, Any] | None = None,
    client: str | None = None,
) -> dict[str, Any]:
    """Store raw/visible transcript content for the current session.

    This is for raw user prompts, visible assistant responses, tool/system
    snippets, and explicit reasoning summaries. Hidden chain-of-thought should
    never be stored; use role='reasoning_summary' for a concise visible
    reasoning summary instead.
    """
    _ensure_bootstrapped()
    client_name = _client_name(client)
    session_id, session_key = _current_context(client_name)
    entry = transcript_store.log_entry(
        session_id=session_id,
        client=client_name,
        session_key=session_key,
        role=role,
        content=content,
        source_event=source_event,
        metadata=metadata,
    )
    if entry is None:
        return {
            "logged": False,
            "reason": "duplicate_within_dedup_window",
            "session_id": session_id,
            "session_key": session_key,
        }
    return {"logged": True, "entry": entry.to_dict()}


def get_transcript(client: str | None = None, limit: int = 200) -> dict[str, Any]:
    _ensure_bootstrapped()
    client_name = _client_name(client)
    session_key = sessions.derive_session_key()
    try:
        sid = sessions.current_session_id(client_name, session_key=session_key, open_if_missing=False)
    except RuntimeError:
        return {"client": client_name, "session_key": session_key, "session_id": None, "entries": []}
    entries = transcript_store.current_entries(sid, limit=limit)
    return {
        "client": client_name,
        "session_key": session_key,
        "session_id": sid,
        "count": len(entries),
        "entries": [e.to_dict() for e in entries],
    }


def end_progress(
    distill_to_fragment: bool = False,
    summary: str | None = None,
    tags: Sequence[str] | None = None,
    client: str | None = None,
) -> dict[str, Any]:
    """Close the current session for `client` and optionally distill a fragment."""
    _ensure_bootstrapped()
    client_name = _client_name(client)

    try:
        sid = sessions.current_session_id(client_name, open_if_missing=False)
    except RuntimeError:
        return {"rotated": False, "reason": "no_active_session", "client": client_name}

    entries = ledger_store.current_entries(sid)
    wiki_log_result = _append_session_summary_to_wiki(sid, client_name, entries, summary) if entries else None
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

    handoff_path, _ = _write_session_handoff(
        sid, client_name, entries, status="completed", final_summary=summary
    )

    new_sid = sessions.rotate(client_name)
    _refresh_working_block(client_name)

    return {
        "rotated": True,
        "previous_session_id": sid,
        "new_session_id": new_sid,
        "client": client_name,
        "distilled_fragment": stored_fragment,
        "handoff_path": handoff_path,
        "wiki_log": wiki_log_result,
    }


def auto_end_idle_sessions() -> dict[str, Any]:
    """Close any open session whose last activity exceeds `auto_end_idle_minutes`.

    v1.6.0: also auto-distills any closed session that has at least
    `auto_distill_min_entries` ledger entries into a `source_type=session-summary`
    fragment. This turns the 12.9% distillation rate measured pre-1.6 into
    something close to "every meaningful session leaves a durable trace".

    Called from the decay cycle, so it runs at most once per hour.
    """
    _ensure_bootstrapped()
    minutes = config.get_setting("auto_end_idle_minutes")
    if not minutes:  # None or 0 disables
        return {"ended": 0, "reason": "disabled"}
    minutes_int = int(minutes)
    min_entries = int(config.get_setting("auto_distill_min_entries") or 0)

    ended: list[dict[str, Any]] = []
    for sid, client, session_key in sessions.idle_sessions(minutes_int):
        distilled_id: str | None = None
        try:
            entries = ledger_store.current_entries(sid)
        except Exception:
            entries = []
        if min_entries > 0 and len(entries) >= min_entries:
            try:
                summary = _derive_summary(entries)
                content = _render_ledger_as_fragment(entries, explicit_summary=summary)
                frag = frag_store.create(
                    content=content,
                    summary=summary,
                    tags=["session-summary", client, "auto-distilled"],
                    source_type="session-summary",
                    source_ref=sid,
                )
                distilled_id = frag.id
                try:
                    from hippocampus.embeddings import search as semantic_search
                    semantic_search.upsert_for_fragment(frag.id)
                except Exception:
                    pass
            except Exception:
                distilled_id = None

        _write_session_handoff(sid, client, entries, status="auto-closed")
        sessions.close_session(sid)
        sessions.open_session(client, session_key=session_key)
        _refresh_working_block(client, session_key=session_key)
        ended.append({
            "session_id": sid,
            "client": client,
            "session_key": session_key,
            "entries": len(entries),
            "distilled_fragment_id": distilled_id,
        })
    return {"ended": len(ended), "minutes": minutes_int, "sessions": ended}


# ---------------------------------------------------------------------------
# Wiki tools (V10)
# ---------------------------------------------------------------------------


def wiki_init(
    project: str | None = None,
    title: str | None = None,
    workspace_path: str | None = None,
    export_root: str | None = None,
    materialize: bool = False,
) -> dict[str, Any]:
    """Initialize database-backed wiki state for a project."""
    _ensure_bootstrapped()
    from hippocampus.wiki import workspace

    return workspace.init_project(
        project=project,
        title=title,
        workspace_path=workspace_path,
        export_root=export_root,
        materialize=materialize,
    )


def wiki_status(project: str | None = None) -> dict[str, Any]:
    _ensure_bootstrapped()
    from hippocampus.wiki import projects, storage

    key = projects.derive_project_key(project)
    p = storage.get_project_by_key(key)
    if p is None:
        from hippocampus.wiki.models import blocked_not_initialized

        return blocked_not_initialized(key, workspace_path=projects.current_workspace())
    pages = storage.list_pages(p.id, limit=10_000)
    sources = storage.list_sources(p.id, limit=10_000)
    logs = storage.list_log(p.id, limit=1)
    return {
        "ok": True,
        "blocked": False,
        "project": p.to_dict(),
        "pages": len(pages),
        "sources": len(sources),
        "has_log": bool(logs),
    }


def wiki_lint(project: str | None = None) -> dict[str, Any]:
    _ensure_bootstrapped()
    from hippocampus.wiki import lint

    return lint.run(project=project)


def wiki_export(project: str | None = None) -> dict[str, Any]:
    _ensure_bootstrapped()
    from hippocampus.wiki import export, projects

    p, blocked = projects.require_project(project)
    if blocked:
        return blocked
    assert p is not None
    return export.materialize(p)


def wiki_ingest(
    raw_path: str,
    project: str | None = None,
    dry_run: bool = False,
    materialize: bool = False,
) -> dict[str, Any]:
    _ensure_bootstrapped()
    from hippocampus.wiki import ingest

    return ingest.ingest(raw_path, project=project, dry_run=dry_run, materialize=materialize)


def wiki_query(question: str, project: str | None = None, limit: int | None = None) -> dict[str, Any]:
    _ensure_bootstrapped()
    from hippocampus.wiki import query as wiki_query_mod

    return wiki_query_mod.query(
        question,
        project=project,
        limit=int(limit if limit is not None else config.get_setting("wiki_query_limit") or 8),
    )


def wiki_file_answer(
    title: str,
    markdown: str,
    project: str | None = None,
    materialize: bool = False,
) -> dict[str, Any]:
    _ensure_bootstrapped()
    from hippocampus.wiki import query as wiki_query_mod

    return wiki_query_mod.file_answer(title, markdown, project=project, materialize=materialize)


def wiki_index(project: str | None = None) -> dict[str, Any]:
    _ensure_bootstrapped()
    from hippocampus.wiki import index as wiki_index_mod, projects

    p, blocked = projects.require_project(project)
    if blocked:
        return blocked
    assert p is not None
    page = wiki_index_mod.refresh(p)
    return {"ok": True, "project": p.to_dict(), "page": page.to_dict(), "markdown": page.markdown}


def wiki_log(project: str | None = None, limit: int = 20) -> dict[str, Any]:
    _ensure_bootstrapped()
    from hippocampus.wiki import log as wiki_log_mod, projects, storage

    p, blocked = projects.require_project(project)
    if blocked:
        return blocked
    assert p is not None
    page = wiki_log_mod.refresh(p)
    return {
        "ok": True,
        "project": p.to_dict(),
        "entries": [e.to_dict() for e in storage.list_log(p.id, limit=limit)],
        "page": page.to_dict(),
        "markdown": page.markdown,
    }


def _append_session_summary_to_wiki(
    session_id: str,
    client_name: str,
    entries: list[ledger_store.LedgerEntry],
    explicit_summary: str | None,
) -> dict[str, Any]:
    from hippocampus.wiki import export, log as wiki_log_mod, projects, storage, workspace

    project_key = projects.derive_project_key()
    project = storage.get_project_by_key(project_key)
    initialized = project is None
    if initialized:
        workspace.init_project(
            project=project_key,
            workspace_path=projects.current_workspace(),
            materialize=False,
        )
        project = storage.get_project_by_key(project_key)
    if project is None:
        return {"ok": False, "reason": "wiki_init_failed", "project_key": project_key}

    resolved_summary = (explicit_summary or _derive_summary(entries)).strip() or "Session summary"
    transcript_entries = transcript_store.current_entries(session_id, limit=500)
    lessons = _extract_lessons(entries, transcript_entries)
    details = _render_session_log_details(
        session_id=session_id,
        client_name=client_name,
        summary=resolved_summary,
        entries=entries,
        lessons=lessons,
    )
    entry = storage.append_log(
        project.id,
        kind="session-summary",
        title=resolved_summary,
        details=details,
        metadata={
            "session_id": session_id,
            "client": client_name,
            "lessons": lessons,
        },
    )
    page = wiki_log_mod.refresh(project)
    written_paths: list[str] = []
    if bool(config.get_setting("wiki_materialize_default")):
        written_paths = export.materialize(project).get("written_paths", [])
    return {
        "ok": True,
        "initialized": initialized,
        "project": project.to_dict(),
        "entry": entry.to_dict(),
        "page": page.to_dict(),
        "lessons": lessons,
        "written_paths": written_paths,
    }


def _derive_summary(entries: list[ledger_store.LedgerEntry]) -> str:
    goal = next((e for e in entries if e.kind == "goal"), None)
    if goal:
        return f"Session summary: {goal.content[:120]}"
    first_ask = next((e for e in entries if e.kind == "ask"), None)
    if first_ask:
        return f"Session summary: {first_ask.content[:120]}"
    return "Session summary"


def _render_ledger_as_fragment(entries: list[ledger_store.LedgerEntry], *, explicit_summary: str | None) -> str:
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


def _extract_lessons(entries: list[ledger_store.LedgerEntry], transcript_entries: Sequence[Any]) -> list[str]:
    lessons: list[str] = []
    for e in entries:
        if e.kind in {"decision", "blocker"}:
            lessons.append(f"{e.kind}: {e.content}")
        elif e.kind == "note" or _looks_like_lesson(e.content):
            lessons.append(e.content)
    for e in transcript_entries:
        if e.role in {"assistant_summary", "reasoning_summary", "user"} and _looks_like_lesson(e.content):
            lessons.append(_first_line(e.content))
    return _unique(lessons)[:12]


def _looks_like_lesson(text: str) -> bool:
    return bool(re.search(r"\b(learned|lesson|remember|prefer|avoid|always|never|must|should)\b", text, re.I))


def _first_line(text: str, limit: int = 180) -> str:
    line = next(iter((text or "").strip().splitlines()), "").strip()
    return line[:limit].rstrip()


def _unique(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        cleaned = item.strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _render_session_log_details(
    *,
    session_id: str,
    client_name: str,
    summary: str,
    entries: list[ledger_store.LedgerEntry],
    lessons: list[str],
) -> str:
    lines = [
        f"Session: `{session_id}`",
        f"Client: `{client_name}`",
        "",
        "### Summary",
        "",
        summary,
        "",
    ]
    if lessons:
        lines.extend(["### Lessons Learned", ""])
        lines.extend(f"- {lesson}" for lesson in lessons)
        lines.append("")
    lines.extend(["### Ledger", "", _render_ledger_as_fragment(entries, explicit_summary=None)])
    return "\n".join(lines).strip()
