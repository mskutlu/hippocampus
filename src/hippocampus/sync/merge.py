"""Pure merge rules for device sync (V11). No I/O; shared by client tests.

A fragment record is a plain dict with the `fragments` columns plus
`tags` (list[str]). `updated_at` is the content timestamp (bumped only by
content/summary/pinned/project/tag changes, not by boosts), so "newer
updated_at wins" is a real edit-ordering rule. Dynamics fields merge
monotonically: accessed/confidence/last_accessed_at take the max.
"""

from __future__ import annotations

from typing import Any

CONTENT_FIELDS = ("content", "summary", "source_type", "source_ref", "pinned", "project")
ARCHIVE_THRESHOLD = 0.05


def _ts(value: str | None) -> str:
    return value or ""


def merge_fragment(local: dict[str, Any] | None, remote: dict[str, Any]) -> dict[str, Any]:
    """Return the row to store. With no local copy the remote wins outright."""
    if local is None:
        out = dict(remote)
        out["tags"] = sorted(set(remote.get("tags") or []))
        return out

    remote_newer = _ts(remote.get("updated_at")) > _ts(local.get("updated_at"))
    base = remote if remote_newer else local
    out = {k: base.get(k) for k in CONTENT_FIELDS}
    out["id"] = local["id"]
    out["updated_at"] = max(_ts(local.get("updated_at")), _ts(remote.get("updated_at"))) or None
    out["created_at"] = min(
        [t for t in (_ts(local.get("created_at")), _ts(remote.get("created_at"))) if t] or [""]
    ) or None
    out["accessed"] = max(int(local.get("accessed") or 0), int(remote.get("accessed") or 0))
    out["confidence"] = max(float(local.get("confidence") or 0.0), float(remote.get("confidence") or 0.0))
    out["last_accessed_at"] = max(_ts(local.get("last_accessed_at")), _ts(remote.get("last_accessed_at"))) or None
    if out["confidence"] >= ARCHIVE_THRESHOLD:
        out["below_threshold_since"] = None
    else:
        flags = [t for t in (local.get("below_threshold_since"), remote.get("below_threshold_since")) if t]
        out["below_threshold_since"] = min(flags) if flags else None
    out["tags"] = sorted(set(local.get("tags") or []) | set(remote.get("tags") or []))
    return out


def changed(local: dict[str, Any] | None, merged: dict[str, Any]) -> bool:
    if local is None:
        return True
    keys = (*CONTENT_FIELDS, "updated_at", "accessed", "confidence", "last_accessed_at", "below_threshold_since")
    if any(local.get(k) != merged.get(k) for k in keys):
        return True
    return sorted(set(local.get("tags") or [])) != merged.get("tags")


def tombstone_wins(local: dict[str, Any] | None, deleted_at: str) -> bool:
    """A delete beats a local copy unless the copy was edited after the delete."""
    if local is None:
        return False
    return _ts(deleted_at) >= _ts(local.get("updated_at"))


def merge_association(local: dict[str, Any] | None, remote: dict[str, Any]) -> dict[str, Any]:
    if local is None:
        return dict(remote)
    return {
        "fragment_a": local["fragment_a"],
        "fragment_b": local["fragment_b"],
        "weight": max(float(local.get("weight") or 0.0), float(remote.get("weight") or 0.0)),
        "co_accessed_count": max(int(local.get("co_accessed_count") or 0), int(remote.get("co_accessed_count") or 0)),
        "last_co_accessed_at": max(_ts(local.get("last_co_accessed_at")), _ts(remote.get("last_co_accessed_at"))) or None,
    }
