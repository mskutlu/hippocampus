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


def looks_like_negation(prompt: str) -> bool:
    """Return True iff the prompt opens with a recognised negation marker."""
    if not prompt:
        return False
    return bool(NEGATION_RE.search(prompt))


def recently_boosted_fragment(session_id: str, *, max_turns: int) -> Optional[str]:
    """Find the most recent fragment boosted via log_progress / recall in this session.

    The window is the *broader* of:
      - this session's `started_at`
      - the timestamp of the (max_turns)-th-most-recent ledger entry

    This makes sure we still catch a boost that happened before any ledger
    entry was logged (e.g. a `recall` call kicked off the session implicitly).
    Only boost rows whose `reason` starts with `log_progress`, `recall`, or
    `cluster:` count — these are the kinds the user would want to take back.
    """
    from hippocampus.storage.db import get_ro_conn

    with get_ro_conn() as conn:
        # Session start — the natural lower bound for "this session's boosts".
        sess_row = conn.execute(
            "SELECT started_at FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        session_start = sess_row["started_at"] if sess_row else None

        # Last N ledger entries — gives a tighter window when the session is long.
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

        # Use the EARLIER bound so we don't miss boosts that happened before
        # the ledger was first written.
        candidates = [c for c in (session_start, ledger_cutoff) if c]
        if not candidates:
            return None
        cutoff = min(candidates)

        row = conn.execute(
            """
            SELECT fragment_id FROM feedback_log
            WHERE kind = 'boost'
              AND created_at >= ?
              AND (
                reason LIKE 'log_progress%'
                OR reason LIKE 'recall%'
                OR reason LIKE 'cluster:%'
              )
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (cutoff,),
        ).fetchone()
    return row["fragment_id"] if row else None


def infer_and_forget(prompt: str, *, session_id: str) -> Optional[str]:
    """Top-level entry point. Returns the demoted fragment_id, or None."""
    if not bool(config.get_setting("inferred_negation_enabled")):
        return None
    if not looks_like_negation(prompt):
        return None
    max_turns = int(config.get_setting("inferred_negation_window_turns") or 2)
    target = recently_boosted_fragment(session_id, max_turns=max_turns)
    if not target:
        return None
    from hippocampus.dynamics import boost as boost_dyn
    boost_dyn.apply_negative_feedback(target, reason="user_negation_inferred")
    return target
