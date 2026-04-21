"""Hippocampus MCP server — stdio transport.

Exposes the 8 tools from `hippocampus.mcp.tools` to any MCP-compatible AI
client (Devin, Claude Code, OpenCode, Windsurf, Antigravity, ...).

Logging goes to stderr + a log file only — never stdout, which is reserved for
JSON-RPC messages by the stdio transport.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from hippocampus import config
from hippocampus.mcp import tools as T

# ---------------------------------------------------------------------------
# Logging setup (stderr only)
# ---------------------------------------------------------------------------

_log_path = config.LOG_DIR / f"mcp-{datetime.now(timezone.utc):%Y-%m-%d}.log"
config.ensure_dirs()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(_log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("hippocampus.mcp")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOL_SPECS: list[Tool] = [
    Tool(
        name="recall",
        description=(
            "Search synthesized memory fragments. "
            "Every returned fragment is boosted (+0.015 confidence, access counter +1, "
            "co-access associations strengthened, context_tag attached). "
            "Use this when you need to retrieve what you or the user already knows."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text FTS query"},
                "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 50},
                "min_confidence": {"type": "number", "default": 0.0, "minimum": 0.0, "maximum": 1.0},
                "context_tag": {
                    "type": "string",
                    "description": "Optional tag (e.g. 'debugging') added to every hit",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="remember",
        description=(
            "Store a synthesized fragment (NOT raw conversation). "
            "Only distilled, atomic ideas belong here. "
            "New fragments start at confidence=0.5."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The synthesized fragment content"},
                "summary": {"type": "string", "description": "One-line summary (optional; auto if blank)"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source_type": {"type": "string", "description": "e.g. 'session', 'decision', 'manual'"},
                "source_ref": {"type": "string", "description": "Pointer to origin (path, URL, session id)"},
                "pinned": {"type": "boolean", "default": False, "description": "Shield from decay"},
            },
            "required": ["content"],
        },
    ),
    Tool(
        name="forget",
        description=(
            "Apply negative feedback to a fragment (-0.02 confidence). "
            "Use when a recalled fragment turns out to be wrong or stale."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "fragment_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["fragment_id"],
        },
    ),
    Tool(
        name="pin",
        description="Mark a fragment as pinned so it never decays.",
        inputSchema={
            "type": "object",
            "properties": {"fragment_id": {"type": "string"}},
            "required": ["fragment_id"],
        },
    ),
    Tool(
        name="unpin",
        description="Remove the pinned flag from a fragment.",
        inputSchema={
            "type": "object",
            "properties": {"fragment_id": {"type": "string"}},
            "required": ["fragment_id"],
        },
    ),
    Tool(
        name="get_fragment",
        description=(
            "Read a single fragment by id. "
            "Boosts confidence unless boost_on_read=false."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "fragment_id": {"type": "string"},
                "boost_on_read": {"type": "boolean", "default": True},
            },
            "required": ["fragment_id"],
        },
    ),
    Tool(
        name="list_fragments",
        description=(
            "Administrative listing (no boost). "
            "Filter by tag and/or minimum confidence."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "tag": {"type": "string"},
                "min_confidence": {"type": "number", "default": 0.0},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
            },
        },
    ),
    Tool(
        name="top_fragments",
        description=(
            "Return the top-N highest-ranking fragments (by confidence × recency). "
            "Used for auto-injection; does not apply boost."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 15, "minimum": 1, "maximum": 100},
            },
        },
    ),
    Tool(
        name="get_stats",
        description="Health dashboard: counts, average confidence, recent feedback events.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="log_progress",
        description=(
            "WORKING MEMORY — append one entry to the current session's ledger. "
            "Call this reflexively: every ask -> log_progress(kind='ask', ...); "
            "every completed action -> kind='done'; decisions -> 'decision'; "
            "blockers -> 'blocker'; planned next steps -> 'next'; goal changes -> 'goal'; "
            "other context -> 'note'. "
            "The entry survives compaction because the WORKING block is re-injected "
            "into the client's always-on rules file on every turn. "
            "Any frag_... ids referenced in content are boosted as if recalled. "
            "Dedup window: identical entries within 60s are merged."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["goal", "ask", "done", "blocker", "decision", "next", "note"],
                },
                "content": {
                    "type": "string",
                    "description": "One-line synthesis of the event (not raw user text).",
                },
                "details": {"type": "string", "description": "Optional longer context."},
            },
            "required": ["kind", "content"],
        },
    ),
    Tool(
        name="get_progress",
        description=(
            "Return the current session's ledger (or full history). "
            "Call this when you need more detail than the injected WORKING block shows."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "full": {"type": "boolean", "default": False},
                "client": {"type": "string"},
            },
        },
    ),
    Tool(
        name="end_progress",
        description=(
            "Close the current session and optionally distill the whole ledger into a "
            "single long-term fragment. Call this when the task is complete. "
            "The next log_progress call will start a fresh session."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "distill_to_fragment": {"type": "boolean", "default": False},
                "summary": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
    ),
    Tool(
        name="undo_last_entry",
        description=(
            "Pop the most recent ledger entry from the current session. "
            "Use this to correct a log_progress mistake. Refuses if the entry "
            "is older than 5 minutes — use end_progress for older corrections."
        ),
        inputSchema={
            "type": "object",
            "properties": {"client": {"type": "string"}},
        },
    ),
]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

TOOL_DISPATCH = {
    "recall": T.recall,
    "remember": T.remember,
    "forget": T.forget,
    "pin": T.pin,
    "unpin": T.unpin,
    "get_fragment": T.get_fragment,
    "list_fragments": T.list_fragments,
    "top_fragments": T.top_fragments,
    "get_stats": T.get_stats,
    "log_progress": T.log_progress,
    "get_progress": T.get_progress,
    "end_progress": T.end_progress,
    "undo_last_entry": T.undo_last_entry,
}


def _text_response(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------

server = Server("hippocampus")


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return TOOL_SPECS


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        log.warning("unknown tool requested: %s", name)
        return _text_response({"error": f"unknown tool: {name}"})
    args = arguments or {}
    log.info("tool=%s args=%s", name, {k: (v if not isinstance(v, str) else v[:80]) for k, v in args.items()})
    try:
        result = fn(**args)
        return _text_response(result)
    except TypeError as e:
        log.exception("bad arguments for %s", name)
        return _text_response({"error": f"bad arguments: {e}"})
    except Exception as e:  # noqa: BLE001 — surface to caller
        log.exception("tool failed: %s", name)
        return _text_response({"error": str(e), "tool": name})


async def _run() -> None:
    # Ensure DB + vault dirs exist before the first request arrives.
    T._ensure_bootstrapped()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    import asyncio

    asyncio.run(_run())


if __name__ == "__main__":
    main()
