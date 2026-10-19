"""Negation inference — auto-`forget()` when the user pushes back on a recall hit.

V9 G7 / B4. Conservative on purpose:

1. We only act when the *current* prompt opens with a strong negation marker.
2. We only forget the most recent fragment that was boosted via
   `log_progress_auto:...` or `recall` in this session within the last
   `inferred_negation_window_turns` user turns.
3. We never fire if `inferred_negation_enabled` is false.

Why all the guards? "no" is a common English word; we'd rather miss legit
corrections than wrongly demote a useful fragment. The user can always type
`forget frag_…` explicitly when they want a hard correction.
"""

from __future__ import annotations

import re
from typing import Optional

from hippocampus import config


# Open-the-prompt negation markers. Multilingual (EN + TR) because the user mixes.
NEGATION_RE = re.compile(
    r"^\s*("
    r"no[,.!?\s]|"
    r"nope[,.!?\s]|"
    r"wrong[,.!?\s]|"
    r"that[\u2019']?s\s+(?:not|wrong)|"
    r"that\s+is\s+(?:not|wrong)|"
    r"actually,?\s+(?:no|not)|"
    r"hay[ı i]r[,.!?\s]?|"
    r"yanl[ı i]ş[,.!?\s]?"
    r")",
    re.IGNORECASE,
)


CORRECTION_RE = re.compile(
    r"\bstill\s+(\w+\s+){0,3}"
    r"(not|do(es)?\s*n'?o?t|broken|failing|fail|wrong|same|getting|seeing|sees|gets|happen)|"
    r"\bnot\s+fixed|"
    r"(it'?s|is)\s+still\s+(broken|wrong)|"
    r"(revert|undo)\s+(that|this|it)\b|"
    r"wrong\s+(again|value|result|file)|"
    r"\bhala\b|hâlâ|olmad[ıi]\b|düzelmedi|çalışmıyor|geri al\b|yanlış",
    re.IGNORECASE,
)
FAILED_RE = re.compile(
    r"(didn'?t|doesn'?t|does not|did not|doesnt|didnt)\s+(work|fix|help|change)", re.IGNORECASE
)
REPEAT_RE = re.compile(r"\b(still|again|same|yine|hala)\b", re.IGNORECASE)
CORRECTION_EXCLUDE_RE = re.compile(
    r"dont broke|i may revert|reset the vpn|^\s*<|^\s*if\b|^\s*(can|could|does|is|did)\b[^?]*\?",
    re.IGNORECASE,
)
CORRECTION_SCAN_CHARS = 120


def looks_like_correction(prompt: str) -> bool:
    """True iff the first ~120 chars say the previous result did not work."""
    if not prompt:
        return False
    head = " ".join(prompt.split())[:CORRECTION_SCAN_CHARS]
    if CORRECTION_EXCLUDE_RE.search(head):
        return False
    return bool(CORRECTION_RE.search(head)) or bool(FAILED_RE.search(head) and REPEAT_RE.search(head))


def looks_like_negation(prompt: str) -> bool:
    """Return True iff the prompt opens with a recognised negation marker."""
    if not prompt:
        return False
    return bool(NEGATION_RE.search(prompt))


def _window_cutoff(conn, session_id: str, max_turns: int) -> Optional[str]:
    sess_row = conn.execute(
        "SELECT started_at FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    session_start = sess_row["started_at"] if sess_row else None

    ledger_count = conn.execute(
        "SELECT COUNT(*) AS count FROM session_ledger WHERE session_id = ?",
        (session_id,),
    ).fetchone()["count"]
    cutoff_row = conn.execute(
        """
        SELECT MIN(created_at) AS cutoff FROM (
            SELECT created_at FROM session_ledger
            WHERE session_id = ?
            ORDER BY created_at DESC LIMIT ?
        )
        """,
        (session_id, max(1, max_turns)),
    ).fetchone()
    ledger_cutoff = cutoff_row["cutoff"] if cutoff_row and cutoff_row["cutoff"] else None
    return ledger_cutoff if ledger_count > max_turns else session_start


def recently_boosted_fragment(session_id: str, *, max_turns: int) -> Optional[str]:
    """Find the most recent fragment boosted via log_progress / recall in this session.

    Only boost rows whose `reason` starts with `log_progress`, `recall`, or
    `cluster:` count — these are the kinds the user would want to take back.
    """
    from hippocampus.storage.db import get_ro_conn

    with get_ro_conn() as conn:
        cutoff = _window_cutoff(conn, session_id, max_turns)
        if not cutoff:
            return None

        row = conn.execute(
            """
            SELECT fragment_id FROM feedback_log
            WHERE kind = 'boost'
              AND session_id = ?
              AND created_at >= ?
              AND (
                reason LIKE 'log_progress%'
                OR reason LIKE 'recall%'
                OR reason LIKE 'cluster:%'
              )
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id, cutoff),
        ).fetchone()
    return row["fragment_id"] if row else None


def recently_recalled_fragment(session_id: str, *, max_turns: int) -> Optional[str]:
    """Most recent fragment actually recalled (via='recall') inside the turn window."""
    from hippocampus.storage.db import get_ro_conn

    with get_ro_conn() as conn:
        cutoff = _window_cutoff(conn, session_id, max_turns)
        if not cutoff:
            return None
        row = conn.execute(
            """
            SELECT fragment_id FROM session_accesses
            WHERE session_id = ? AND via = 'recall' AND accessed_at >= ?
            ORDER BY accessed_at DESC
            LIMIT 1
            """,
            (session_id, cutoff),
        ).fetchone()
    return row["fragment_id"] if row else None


def infer_and_forget(prompt: str, *, session_id: str) -> Optional[str]:
    """Top-level entry point. Returns the demoted fragment_id, or None."""
    if not bool(config.get_setting("inferred_negation_enabled")):
        return None
    if not looks_like_negation(prompt):
        return None
    max_turns = int(config.get_setting("inferred_negation_window_turns") or 2)
    from hippocampus.dynamics import outcomes
    if outcomes.evidence_mode():
        return _hurt_recalled(session_id, max_turns)
    if outcomes.shadow_mode():
        try:
            shadow_target = recently_recalled_fragment(session_id, max_turns=max_turns)
            if shadow_target:
                outcomes.shadow_apply(session_id, shadow_target, "hurt", "medium", "negation")
        except Exception:
            pass
    target = recently_boosted_fragment(session_id, max_turns=max_turns)
    if not target:
        return None
    from hippocampus.dynamics import boost as boost_dyn
    boost_dyn.apply_negative_feedback(
        target,
        reason="user_negation_inferred",
        session_id=session_id,
    )
    return target


def _hurt_recalled(session_id: str, max_turns: int) -> Optional[str]:
    from hippocampus.dynamics import outcomes
    from hippocampus.storage import feedback

    target = recently_recalled_fragment(session_id, max_turns=max_turns)
    if not target:
        return None
    delta = outcomes.apply(session_id, target, "hurt", "medium", "negation")
    if delta is None:
        return None
    feedback.log(
        target, "negative", delta=delta, reason="user_negation_inferred", session_id=session_id
    )
    return target
