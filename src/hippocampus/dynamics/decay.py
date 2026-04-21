"""Session-scoped decay loop.

Rules (exactly per spec):

* Per decay cycle, for every fragment:
    - if pinned: no change.
    - if accessed in the current OR previous session: no change (shield).
    - else: confidence -= DECAY_DELTA (floor CONFIDENCE_MIN).
* Time is never used — a fragment does not decay just because wall-clock time
  passes. Decay only happens when a cycle is explicitly run (launchd hourly).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from hippocampus import config
from hippocampus.storage import feedback, fragments as frag_store, sessions


@dataclass
class DecayResult:
    fragments_scanned: int = 0
    fragments_decayed: int = 0
    fragments_shielded: int = 0
    fragments_pinned_skipped: int = 0
    fragments_flagged_for_archive: int = 0

    def as_dict(self) -> dict:
        return {
            "fragments_scanned": self.fragments_scanned,
            "fragments_decayed": self.fragments_decayed,
            "fragments_shielded": self.fragments_shielded,
            "fragments_pinned_skipped": self.fragments_pinned_skipped,
            "fragments_flagged_for_archive": self.fragments_flagged_for_archive,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def run_decay_cycle(*, dry_run: bool = False) -> DecayResult:
    """Apply one decay cycle across the whole DB.

    Also calls `auto_end_idle_sessions()` (no-op unless the user configured
    `auto_end_idle_minutes`), so long-idle sessions rotate cleanly and the
    WORKING block stops showing stale state.

    Shield window = current + previous session (most recent 2 session ids
    across all clients). This is generous on purpose: if the user did anything
    in the last session, their memories are safe for one more cycle.
    """
    # Rotate idle sessions BEFORE decay so the shield window reflects them.
    try:
        from hippocampus.mcp import tools as _tools
        if not dry_run:
            _tools.auto_end_idle_sessions()
    except Exception:
        # Decay must not fail because of idle-ender bookkeeping.
        pass

    shield = sessions.accessed_fragment_ids_in_sessions(sessions.last_n_session_ids(n=2))

    result = DecayResult()
    now_iso = _utc_now()

    for frag in frag_store.iter_all():
        result.fragments_scanned += 1

        if frag.pinned:
            result.fragments_pinned_skipped += 1
            continue

        if frag.id in shield:
            result.fragments_shielded += 1
            continue

        new_conf = max(config.CONFIDENCE_MIN, frag.confidence - config.DECAY_DELTA)
        if new_conf == frag.confidence:
            # already at floor; nothing to log
            continue

        if not dry_run:
            # Track when the fragment first went under ARCHIVE_THRESHOLD.
            below_flag: str | None | bool = False  # False = don't touch
            if new_conf < config.ARCHIVE_THRESHOLD and frag.below_threshold_since is None:
                below_flag = now_iso
                result.fragments_flagged_for_archive += 1
            elif new_conf >= config.ARCHIVE_THRESHOLD and frag.below_threshold_since is not None:
                below_flag = None  # recovered — clear flag

            frag_store.update_fields(frag.id, confidence=new_conf, below_threshold_since=below_flag)
            feedback.log(frag.id, "decay", delta=-config.DECAY_DELTA, reason=None)

        result.fragments_decayed += 1

    return result
