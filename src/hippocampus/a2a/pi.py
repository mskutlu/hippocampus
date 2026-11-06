"""Pi tool lockdown: hidden MCP methods the pi extension calls around a peer turn.

`a2a_check_tool` maps a pi tool call onto guard.decide() (the Claude Code PreToolUse decision);
`a2a_end_turn` ends the session's peer turn at pi's agent_end. Neither is listed in TOOL_SPECS, and
server.handle_call_tool serves them only when HIPPOCAMPUS_CLIENT=pi.
"""

from __future__ import annotations

from typing import Any

from hippocampus import a2a
from hippocampus.a2a import guard, policy, state, tools as A2A
from hippocampus.mcp import tools as T

HIDDEN = ("a2a_check_tool", "a2a_end_turn")
PI_CLIENT = "pi"
# pi built-in read-type tools -> (Claude name, {pi input key: Claude input key}); everything else is denied by decide().
READ_MAP = {
    "read": ("Read", {"path": "file_path"}),
    "grep": ("Grep", {"path": "path", "glob": "glob"}),
    "find": ("Glob", {"path": "path", "pattern": "pattern"}),
    "ls": ("LS", {"path": "path"}),
}
HIPPO_TOOLS = frozenset({"recall", "get_fragment", "list_fragments", "top_fragments", "inbox", "reply",
                         "list_agents", "get_progress", "log_progress"} | {"remember", "send_message"})


def map_tool(tool_name: str, tool_input: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if tool_name in READ_MAP:
        claude, keys = READ_MAP[tool_name]
        return claude, {dst: tool_input[src] for src, dst in keys.items() if tool_input.get(src) is not None}
    if tool_name in HIPPO_TOOLS:  # the extension registers hippocampus tools under their bare names
        return f"mcp__hippocampus__{tool_name}", tool_input
    return tool_name, tool_input  # bash/edit/write/powershell/unknown: denied


def check_tool(tool_name: str, tool_input: dict | None = None, cwd: str = "") -> dict:
    if not a2a.enabled():
        return {"allow": True}
    _, key = A2A._me()
    turn = state.active_peer_turn(key)
    if not turn:
        return {"allow": True}
    name, args = map_tool(str(tool_name), tool_input or {})
    reason = guard.decide(name, args, cwd, cwd, turn, policy.for_sender(turn["sender"]))
    return {"allow": False, "reason": reason} if reason else {"allow": True}


def end_turn() -> dict:
    if a2a.enabled():
        state.end_peer_turn(A2A._me()[1])
    return {"ended": True}


def is_pi() -> bool:
    return T._client_name() == PI_CLIENT
