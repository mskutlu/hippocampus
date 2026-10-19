"""Evidence-driven confidence: record what recalled fragments did for a session.

Active only when `confidence_model` is `evidence`. Every outcome is written to
`fragment_outcomes` first (INSERT OR IGNORE on the primary key), so replays and
double settlement never apply twice.

* helped: c += r * (1 - c); weak helped is skipped at/above the implicit ceiling
* hurt:   c = max(0, c - r)
* helped_count counts medium/strong helped only; hurt_count counts every hurt
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from hippocampus import config
from hippocampus.dynamics.negation import looks_like_correction
from hippocampus.storage import feedback, fragments as frag_store
from hippocampus.storage.db import get_conn, get_ro_conn

NO_SESSION = "no-session"
SHADOW_PREFIX = "shadow:"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _model() -> str:
    return str(config.get_setting("confidence_model") or "access").lower()


def evidence_mode() -> bool:
    return _model() == "evidence"


def shadow_mode() -> bool:
    return _model() == "shadow"


def recording_active() -> bool:
    return _model() in ("evidence", "shadow")


def _rate(outcome: str, strength: str) -> float:
    return float(config.get_setting(f"evidence_{outcome}_{strength}"))


def compress(confidence: float, factor: float) -> float:
    """Pull confidence above 0.5 toward 0.5 by `factor`."""
    if confidence <= 0.5:
        return confidence
    return 0.5 + (confidence - 0.5) * factor


def _step(confidence: float, outcome: str, strength: str, scale: float) -> float:
    if outcome == "helped":
        if strength == "weak" and confidence >= float(config.get_setting("evidence_implicit_ceiling")):
            return 0.0
        return _rate(outcome, strength) * scale * (1.0 - confidence)
    if outcome == "hurt":
        return -min(confidence, _rate(outcome, strength) * scale)
    return 0.0


def _record(session_id: str, fragment_id: str, outcome: str, strength: str, source: str, delta: float) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO fragment_outcomes
                (session_id, fragment_id, outcome, strength, source, delta)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, fragment_id, outcome, strength, source, delta),
        )
        return cur.rowcount > 0


def apply(
    session_id: str | None,
    fragment_id: str,
    outcome: str,
    strength: str,
    source: str,
    *,
    scale: float = 1.0,
) -> float | None:
    """Record and apply one outcome. Returns the confidence delta, or None if skipped."""
    current = frag_store.get(fragment_id)
    if current is None:
        return None
    delta = _step(current.confidence, outcome, strength, scale)
    shadow = shadow_mode()
    stored_source = f"{SHADOW_PREFIX}{source}" if shadow else source
    if not _record(session_id or NO_SESSION, fragment_id, outcome, strength, stored_source, delta):
        return None
    if shadow:
        return delta
    if outcome == "neutral":
        return 0.0
    helped = 1 if outcome == "helped" and strength != "weak" else 0
    hurt = 1 if outcome == "hurt" else 0
    if delta == 0.0 and not helped and not hurt:
        return 0.0
    updated = frag_store.update_fields(
        fragment_id,
        confidence=current.confidence + delta,
        helped_delta=helped,
        hurt_delta=hurt,
        evidence_at=_utc_now(),
        below_threshold_since=None if outcome == "helped" else False,
    )
    if helped and updated is not None:
        _maybe_auto_pin(updated, session_id)
    return delta


def shadow_apply(*args, **kwargs) -> None:
    """Record a shadow outcome; a failure must never break the access-mode write."""
    try:
        apply(*args, **kwargs)
    except Exception:
        pass


def _maybe_auto_pin(fragment: frag_store.Fragment, session_id: str | None) -> None:
    threshold = int(config.get_setting("auto_pin_min_helped") or 0)
    if (
        threshold > 0
        and not fragment.pinned
        and fragment.review_state == "approved"
        and not fragment.superseded_by
        and fragment.helped_count >= threshold
        and fragment.hurt_count == 0
    ):
        frag_store.update_fields(fragment.id, pinned=True)
        feedback.log(fragment.id, "auto-pin", reason="evidence-helped", session_id=session_id)


def on_done_cites(session_id: str, fragment_ids: Iterable[str]) -> int:
    n = 0
    for fid in fragment_ids:
        if apply(session_id, fid, "helped", "medium", "done_cite") is not None:
            n += 1
    return n


def on_ask(session_id: str, prompt: str, ask_entry_id: int) -> int:
    """A correction ask blames fragments recalled since the previous ask."""
    if not looks_like_correction(prompt):
        return 0
    with get_ro_conn() as conn:
        start = conn.execute(
            "SELECT created_at FROM session_ledger WHERE session_id = ? AND kind = 'ask' AND id < ? "
            "ORDER BY id DESC LIMIT 1",
            (session_id, ask_entry_id),
        ).fetchone()
        if start:
            since = start["created_at"]
        else:
            sess = conn.execute("SELECT started_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
            since = sess["started_at"] if sess else ""
        targets = [
            r["fragment_id"]
            for r in conn.execute(
                "SELECT fragment_id FROM session_accesses "
                "WHERE session_id = ? AND via = 'recall' AND accessed_at >= ? ORDER BY accessed_at DESC",
                (session_id, since),
            ).fetchall()
        ]
    max_targets = max(1, int(config.get_setting("evidence_correction_max_targets") or 1))
    scale = max_targets / len(targets) if len(targets) > max_targets else 1.0
    source = f"correction:{ask_entry_id}"
    return sum(
        apply(session_id, fid, "hurt", "weak", source, scale=scale) is not None for fid in targets
    )


def _record_shadow_settle(session_id: str, batch: list[tuple[str, str]]) -> int:
    with get_conn() as conn:
        conf = {
            r["id"]: float(r["confidence"])
            for r in conn.execute("SELECT id, confidence FROM fragments").fetchall()
        }
        rows = [
            (session_id, fid, outcome, "weak", f"{SHADOW_PREFIX}settle", _step(conf[fid], outcome, "weak", 1.0))
            for fid, outcome in batch
            if fid in conf
        ]
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO fragment_outcomes (session_id, fragment_id, outcome, strength, source, delta) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        return conn.total_changes - before


def settle_session(session_id: str) -> int:
    """Turn a finished session's accesses into outcomes. Idempotent. Returns rows settled."""
    if not recording_active():
        return 0
    with get_ro_conn() as conn:
        row = conn.execute(
            "SELECT outcomes_settled_at FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None or row["outcomes_settled_at"]:
            return 0
        last_done = conn.execute(
            "SELECT MAX(created_at) AS t FROM session_ledger WHERE session_id = ? AND kind = 'done'",
            (session_id,),
        ).fetchone()["t"]
        accesses = conn.execute(
            "SELECT fragment_id, accessed_at, via FROM session_accesses WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        hurt_ids = {
            r["fragment_id"]
            for r in conn.execute(
                "SELECT fragment_id FROM fragment_outcomes WHERE session_id = ? AND outcome = 'hurt'",
                (session_id,),
            ).fetchall()
        }
    settled = 0
    batch: list[tuple] = []
    for a in accesses:
        fid = a["fragment_id"]
        earned = (
            a["via"] == "recall"
            and last_done is not None
            and a["accessed_at"] <= last_done
            and fid not in hurt_ids
        )
        outcome = "helped" if earned else "neutral"
        if shadow_mode():
            batch.append((fid, outcome))
        elif apply(session_id, fid, outcome, "weak", "settle") is not None:
            settled += 1
    if batch:
        settled = _record_shadow_settle(session_id, batch)
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET outcomes_settled_at = ? WHERE id = ?", (_utc_now(), session_id)
        )
    return settled


def mark_ended_settled() -> int:
    """Skip history: treat every ended, unsettled session as already settled."""
    with get_conn() as conn:
        return conn.execute(
            "UPDATE sessions SET outcomes_settled_at = ? WHERE ended_at IS NOT NULL AND outcomes_settled_at IS NULL",
            (_utc_now(),),
        ).rowcount


def settle_pending() -> int:
    """Settle every ended session that has not been settled yet."""
    if not recording_active():
        return 0
    with get_ro_conn() as conn:
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM sessions WHERE ended_at IS NOT NULL AND outcomes_settled_at IS NULL"
            ).fetchall()
        ]
    return sum(settle_session(sid) for sid in ids)

