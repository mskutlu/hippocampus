"""Access-boost dynamics.

On any `recall` or `get_fragment` call:

* confidence += BOOST_DELTA (capped at CONFIDENCE_MAX)
* accessed   += 1
* last_accessed_at = now
* optional context_tag is attached
* session_accesses is logged
* pairwise associations are strengthened across all co-returned ids
* below_threshold_since is cleared (shield against auto-archive)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Sequence

from hippocampus import config
from hippocampus.storage import associations, feedback, fragments as frag_store, sessions


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def boost(
    fragment_id: str,
    *,
    context_tag: str | None = None,
    session_id: str | None = None,
    client: str | None = None,
) -> frag_store.Fragment | None:
    """Apply access boost to a single fragment. Returns the updated fragment."""
    current = frag_store.get(fragment_id)
    if current is None:
        return None
    new_conf = min(config.CONFIDENCE_MAX, current.confidence + config.BOOST_DELTA)
    now = _utc_now()

    add_tags: list[str] = []
    if context_tag and context_tag.strip():
        add_tags.append(context_tag.strip())

    updated = frag_store.update_fields(
        fragment_id,
        confidence=new_conf,
        accessed_delta=1,
        last_accessed_at=now,
        below_threshold_since=None,  # clear any pending-archive flag
        add_tags=add_tags,
    )

    # Figure out which session logs this access. Prefer the explicit
    # session_id, else derive from client pointer, else skip.
    if session_id is None and client:
        session_id = sessions.current_session_id(client, open_if_missing=True)
    if session_id:
        sessions.log_access(session_id, fragment_id)

    feedback.log(fragment_id, "boost", delta=config.BOOST_DELTA, reason=context_tag)
    return updated


def boost_many(
    fragment_ids: Sequence[str],
    *,
    context_tag: str | None = None,
    session_id: str | None = None,
    client: str | None = None,
) -> list[frag_store.Fragment]:
    """Boost every fragment in the list + strengthen all pairwise associations."""
    updated: list[frag_store.Fragment] = []
    for fid in fragment_ids:
        f = boost(fid, context_tag=context_tag, session_id=session_id, client=client)
        if f is not None:
            updated.append(f)
    if len(fragment_ids) > 1:
        associations.strengthen_all(fragment_ids)
    return updated


def apply_negative_feedback(
    fragment_id: str,
    reason: str | None = None,
) -> frag_store.Fragment | None:
    """`forget(id)` — apply -FEEDBACK_DELTA and log it."""
    current = frag_store.get(fragment_id)
    if current is None:
        return None
    new_conf = max(config.CONFIDENCE_MIN, current.confidence - config.FEEDBACK_DELTA)
    updated = frag_store.update_fields(fragment_id, confidence=new_conf)
    feedback.log(fragment_id, "negative", delta=-config.FEEDBACK_DELTA, reason=reason)
    return updated
