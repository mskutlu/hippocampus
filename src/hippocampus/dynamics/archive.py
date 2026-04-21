"""Auto-archive dynamics.

A fragment is a candidate for archival when:

* NOT pinned
* confidence < ARCHIVE_THRESHOLD
* has been below the threshold for ARCHIVE_GRACE_DAYS consecutive days
  (tracked via fragments.below_threshold_since)

Archiving moves the markdown mirror to `Fragments/.archive/` and removes the
SQLite row. The feedback log retains an 'archive' event for provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from hippocampus import config
from hippocampus.storage import feedback, fragments as frag_store


@dataclass
class ArchiveResult:
    fragments_scanned: int = 0
    fragments_archived: int = 0
    fragments_pending: int = 0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_archive_cycle(*, dry_run: bool = False) -> ArchiveResult:
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.ARCHIVE_GRACE_DAYS)
    result = ArchiveResult()

    for frag in frag_store.iter_all():
        result.fragments_scanned += 1
        if frag.pinned:
            continue
        if frag.confidence >= config.ARCHIVE_THRESHOLD:
            continue
        # confidence is below threshold; did it stay there long enough?
        flagged_at = _parse(frag.below_threshold_since)
        if flagged_at is None or flagged_at > cutoff:
            result.fragments_pending += 1
            continue

        if dry_run:
            result.fragments_archived += 1
            continue

        feedback.log(frag.id, "archive", delta=None, reason="auto_archive_low_confidence")
        frag_store.archive(frag.id)
        result.fragments_archived += 1

    return result
