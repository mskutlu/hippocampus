"""Autonomous remembering — detect "remember / always / never / add to rules"
phrases in a user prompt and persist the surrounding context as a fragment.

This module is the heart of V9 G5: "remember without telling things by itself".
The detection is intentionally conservative — we only fire when an explicit
trigger phrase appears, and we always include the trigger in the stored content
so the operator can audit what got remembered and why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from hippocampus import config


# Triggers grouped so the matched group reveals which phrase fired.
TRIGGER_RE = re.compile(
    r"\b("
    r"remember\s+(?:this|that|to)?|"
    r"always(?:\s+do)?|"
    r"never(?:\s+do)?|"
    r"don'?t\s+forget|"
    r"from\s+now\s+on|"
    r"next\s+time|"
    r"add\s+(?:this|it)\s+to\s+(?:the\s+)?(?:global\s+)?rules|"
    r"add\s+to\s+(?:the\s+)?(?:global\s+)?rules|"
    r"keep\s+in\s+mind|"
    r"make\s+sure\s+(?:you|to)"
    r")\b",
    re.IGNORECASE,
)


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZĞÜŞİÖÇ])")


@dataclass
class DetectedRule:
    trigger: str           # the phrase that fired (lowercased)
    sentence: str          # the sentence containing the trigger
    context: str           # 1 sentence before + the trigger sentence + 1 sentence after
    summary: str           # short label derived from `sentence`


def detect(prompt: str) -> Optional[DetectedRule]:
    """Return a DetectedRule iff `prompt` contains an autoremember trigger."""
    if not prompt or len(prompt.strip()) < int(config.get_setting("autoremember_min_chars") or 0):
        return None

    text = prompt.strip()
    m = TRIGGER_RE.search(text)
    if m is None:
        return None
    trigger = m.group(1).lower()

    # Find the sentence containing the trigger by scanning forward/backward.
    sentences = _SENT_SPLIT.split(text)
    # Re-tokenise with the offsets to find which sentence the trigger lives in
    trigger_idx = 0
    running = 0
    for i, s in enumerate(sentences):
        # +1 for the space removed by the split (approximation)
        if running + len(s) >= m.start():
            trigger_idx = i
            break
        running += len(s) + 1

    sentence = sentences[trigger_idx].strip()
    ctx_before = sentences[trigger_idx - 1].strip() if trigger_idx > 0 else ""
    ctx_after = sentences[trigger_idx + 1].strip() if trigger_idx + 1 < len(sentences) else ""
    context = " ".join(p for p in (ctx_before, sentence, ctx_after) if p)

    # Summary = first 120 chars of the trigger sentence, prefixed with the trigger.
    summary_body = sentence[:140]
    summary = f"Rule ({trigger}): {summary_body}".strip()
    return DetectedRule(trigger=trigger, sentence=sentence, context=context, summary=summary)


def auto_remember_from_prompt(prompt: str, *, client: str = "unknown") -> dict | None:
    """If the prompt fires an autoremember trigger, persist a fragment.

    Returns the created fragment dict, or None if no trigger / disabled / dedup.
    Dedup: skip if a fragment with identical `summary` already exists.
    """
    if not bool(config.get_setting("autoremember_enabled")):
        return None
    rule = detect(prompt)
    if rule is None:
        return None

    # Defer imports to keep this module light
    from hippocampus.storage import fragments as frag_store
    from hippocampus.mcp import tools as T

    # Dedup by summary text
    for existing in frag_store.list_all(min_confidence=0.0, limit=500):
        if (existing.summary or "").strip() == rule.summary.strip():
            return None

    tags = [
        "auto-remembered",
        f"client:{client}",
        f"trigger:{rule.trigger.replace(' ', '_')}",
    ]
    res = T.remember(
        content=rule.context,
        summary=rule.summary,
        tags=tags,
        source_type="auto-remembered",
        source_ref=f"prompt:{client}",
        pinned=False,
    )
    return res.get("fragment")
