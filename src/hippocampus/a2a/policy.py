"""Per-peer policy: `answer` (default, locked down), `act` (skip the Claude Code tool lockdown) or `block` (never pushed/shown).

Stdlib + config only: the PreToolUse hook imports this on its hot path.
"""

from __future__ import annotations

import json
from typing import Any

from hippocampus import config

POLICIES = ("answer", "act", "block")


def table() -> dict[str, str]:
    raw: Any = config.get_setting("a2a_peer_policy") or {}
    if isinstance(raw, str):  # HIPPO_A2A_PEER_POLICY env override arrives as text
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return {str(k): v for k, v in raw.items() if v in POLICIES} if isinstance(raw, dict) else {}


def for_sender(address: str) -> str:
    """Exact address first, then its `client@device` base, else `answer`."""
    rules = table()
    for key in (address, (address or "").split("/", 1)[0]):
        if key in rules:
            return rules[key]
    return "answer"


def set_for(address: str, policy: str) -> None:
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    rules = table()
    rules[address] = policy
    config.set_setting("a2a_peer_policy", rules)
