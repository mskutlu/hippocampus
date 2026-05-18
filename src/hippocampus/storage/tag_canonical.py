"""Tag canonicalization — fold near-duplicate tag names into existing canonical forms.

Why: a pre-1.6 audit found 368 of 567 distinct tags (65%) were singletons —
date-specific or task-specific labels that nobody would ever search for again.
This module collapses incoming tags onto existing names when they're similar
enough by either string ratio (difflib) or semantic similarity (embedding cosine).
"""

from __future__ import annotations

import difflib
from typing import Iterable

from hippocampus import config
from hippocampus.storage.db import get_ro_conn


def _existing_tags() -> list[str]:
    with get_ro_conn() as conn:
        rows = conn.execute("SELECT DISTINCT tag FROM fragment_tags").fetchall()
    return [r["tag"] for r in rows]


def _normalize(tag: str) -> str:
    return tag.strip().lower().replace("_", "-")


def _string_match(new: str, existing: list[str], threshold: float) -> str | None:
    if not existing:
        return None
    nn = _normalize(new)
    # Best exact-or-near match first
    candidates = [(_normalize(t), t) for t in existing]
    best_ratio = 0.0
    best_canonical = None
    for norm, original in candidates:
        if norm == nn:
            return original
        ratio = difflib.SequenceMatcher(None, nn, norm).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_canonical = original
    if best_canonical and best_ratio >= threshold:
        return best_canonical
    return None


def canonicalize_one(tag: str, *, threshold: float | None = None) -> str:
    """Return the canonical form of `tag`. Falls back to `tag` if none found."""
    if not tag or not tag.strip():
        return tag
    thr = threshold if threshold is not None else float(
        config.get_setting("tag_canonicalize_threshold") or 0.85
    )
    if thr <= 0:
        return tag
    existing = _existing_tags()
    match = _string_match(tag, existing, thr)
    return match if match else tag


def canonicalize(tags: Iterable[str], *, threshold: float | None = None) -> list[str]:
    """Map a list of tags through canonicalize_one, de-duplicating the output.

    Also folds within-input near-duplicates: the first tag in the batch wins
    and subsequent siblings collapse onto it. Useful when callers throw the
    same tag in twice in different casings.
    """
    thr = threshold if threshold is not None else float(
        config.get_setting("tag_canonicalize_threshold") or 0.85
    )
    existing = _existing_tags()
    out: list[str] = []
    seen_norm: set[str] = set()
    for t in tags:
        if not t or not t.strip():
            continue
        c = canonicalize_one(t, threshold=thr)
        # Check if a previously-emitted tag in this batch matches `c`
        within_match = _string_match(c, out, thr) if thr > 0 else None
        if within_match:
            c = within_match
        if _normalize(c) in seen_norm:
            continue
        seen_norm.add(_normalize(c))
        out.append(c)
    return out
