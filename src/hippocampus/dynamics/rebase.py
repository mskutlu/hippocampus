"""Reversible confidence rebase for the switch to the evidence model.

Compresses access-inflated confidence toward 0.5, re-applies past negatives and
unpins fragments that only the access auto-pin ever pinned. Everything the
rebase overwrites is saved in `confidence_rebase_backup` first.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from hippocampus import config
from hippocampus.dynamics.outcomes import compress
from hippocampus.storage.db import get_conn, get_ro_conn

NEGATION_PENALTY = 0.04
EXPLICIT_PENALTY = 0.20


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _exempt_ids(conn) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT fragment_id FROM feedback_log WHERE kind IN ('pin', 'useful')"
        ).fetchall()
    }


def _penalties(conn) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in conn.execute("SELECT fragment_id, reason FROM feedback_log WHERE kind = 'negative'"):
        cost = NEGATION_PENALTY if r["reason"] == "user_negation_inferred" else EXPLICIT_PENALTY
        out[r["fragment_id"]] = out.get(r["fragment_id"], 0.0) + cost
    return out


def _auto_pinned_only(conn) -> set[str]:
    rows = conn.execute(
        """
        SELECT f.id FROM fragments f
        WHERE f.pinned = 1
          AND EXISTS (SELECT 1 FROM feedback_log a WHERE a.fragment_id = f.id AND a.kind = 'auto-pin')
          AND NOT EXISTS (
              SELECT 1 FROM feedback_log p
              WHERE p.fragment_id = f.id AND p.kind = 'pin'
                AND p.id > (SELECT MAX(a.id) FROM feedback_log a WHERE a.fragment_id = f.id AND a.kind = 'auto-pin')
          )
        """
    ).fetchall()
    return {r[0] for r in rows}


def compute(conn) -> dict[str, Any]:
    """Pure plan: new confidence per changed fragment, plus ids to unpin."""
    factor = float(config.get_setting("rebase_compress"))
    exempt, penalties, unpin = _exempt_ids(conn), _penalties(conn), _auto_pinned_only(conn)
    changes: dict[str, tuple[float, float]] = {}
    total = 0
    for r in conn.execute("SELECT id, confidence FROM fragments"):
        total += 1
        if r["id"] in exempt:
            continue
        old = float(r["confidence"])
        new = max(config.CONFIDENCE_MIN, compress(old, factor) - penalties.get(r["id"], 0.0))
        if abs(new - old) > 1e-12:
            changes[r["id"]] = (old, new)
    return {
        "total": total,
        "exempt": len(exempt),
        "changes": changes,
        "unpin": sorted(unpin - exempt),
    }


def _summary(plan: dict[str, Any]) -> dict[str, Any]:
    deltas = [new - old for old, new in plan["changes"].values()]
    return {
        "fragments": plan["total"],
        "exempt": plan["exempt"],
        "confidence_changed": len(plan["changes"]),
        "mean_change": round(sum(deltas) / len(deltas), 4) if deltas else 0.0,
        "unpinned": len(plan["unpin"]),
    }


def dry_run() -> dict[str, Any]:
    with get_ro_conn() as conn:
        return _summary(compute(conn))


def apply() -> dict[str, Any]:
    now = _utc_now()
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM confidence_rebase_backup LIMIT 1").fetchone():
            raise RuntimeError("a rebase backup already exists; run --restore first")
        plan = compute(conn)
        conn.execute(
            """
            INSERT INTO confidence_rebase_backup (fragment_id, confidence, pinned, accessed, rebased_at)
            SELECT id, confidence, pinned, accessed, ? FROM fragments
            """,
            (now,),
        )
        conn.executemany(
            "UPDATE fragments SET confidence = ?, evidence_at = ? WHERE id = ?",
            [(new, now, fid) for fid, (_, new) in plan["changes"].items()],
        )
        for fid in plan["unpin"]:
            conn.execute(
                "UPDATE fragments SET pinned = 0, updated_at = ?, evidence_at = ? WHERE id = ?",
                (now, now, fid),
            )
            conn.execute(
                "INSERT INTO feedback_log (fragment_id, kind, reason) VALUES (?, 'unpin', 'evidence-rebase')",
                (fid,),
            )
        conn.execute(
            "UPDATE sessions SET outcomes_settled_at = ? WHERE ended_at IS NOT NULL AND outcomes_settled_at IS NULL",
            (now,),
        )
    return _summary(plan)


def restore() -> dict[str, Any]:
    now = _utc_now()
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM confidence_rebase_backup").fetchone()[0]
        conn.execute(
            """
            UPDATE fragments SET
                confidence = (SELECT b.confidence FROM confidence_rebase_backup b WHERE b.fragment_id = fragments.id),
                pinned = (SELECT b.pinned FROM confidence_rebase_backup b WHERE b.fragment_id = fragments.id),
                updated_at = ?, evidence_at = ?
            WHERE id IN (SELECT fragment_id FROM confidence_rebase_backup)
            """,
            (now, now),
        )
        conn.execute("DELETE FROM confidence_rebase_backup")
    return {"restored": n}
