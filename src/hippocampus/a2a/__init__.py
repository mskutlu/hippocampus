"""Agent-to-agent messaging (A2A). Off unless `a2a_enabled` / HIPPOCAMPUS_A2A_ENABLED.

This module stays stdlib + config only: the Claude Code PreToolUse hook imports it.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

from hippocampus import config

_TAG_RE = re.compile(r"<(\s*/?\s*a2a-message)", re.IGNORECASE)


def enabled() -> bool:
    raw = os.environ.get("HIPPOCAMPUS_A2A_ENABLED")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(config.get_setting("a2a_enabled"))


def escape_tag(text: str) -> str:
    """Neutralise any (case/whitespace-variant) a2a-message tag inside untrusted content."""
    return _TAG_RE.sub(r"&lt;\1", text)


def turn_fresh(started_at: str) -> bool:
    """True while a peer turn started at `started_at` is within `a2a_peer_turn_ttl_s`."""
    raw = config.get_setting("a2a_peer_turn_ttl_s")
    ttl = 900 if raw in (None, "") else int(raw)
    try:
        started = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True  # unparseable: keep it active (fail closed)
    return datetime.now(timezone.utc) - started < timedelta(seconds=ttl)
