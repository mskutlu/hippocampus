# PRD V12 — A2A: agent-to-agent messaging

> Status: phase 1 shipped (`b2218d1`, `102d68b`); phases 2-4 proposed
> Author: @msk
> Target version: `1.8.x` (unreleased)
> Replaces: nothing. Builds on v11 (device sync hub, device ids) and v7 (lifecycle hooks).
> Tasks: none; phase 1 landed directly.

## 1. Introduction

Hippocampus already gives every agent on every device one shared memory. The
agents themselves still cannot talk: a Claude Code session on the laptop cannot
ask the pi session on the desktop about the repo it has open, and the owner is
the courier. Memory is the wrong channel for this. Messages are ephemeral,
addressed to one recipient, and carry text written by another agent that must
not be trusted like a memory.

A2A adds a mailbox on the existing sync hub plus the client-side plumbing to
deliver messages into live sessions, with a safety model built around the fact
that a message is a prompt-injection vector.

## 2. Goals

| ID | Goal |
|----|------|
| G1 | An agent on any device can send a message to an agent on any device, addressed `client@device[/session]`. |
| G2 | A running session sees the message without the user relaying it (live push where the client supports it, next-turn notice otherwise). |
| G3 | Memory sync is unchanged: no message ever enters the fragment oplog. |
| G4 | An agent that read a peer message cannot, in that turn, run commands, read secrets, write memory, or message third parties. |
| G5 | Off by default; one kill switch; every send, delivery and rejection is audited. |
| G6 | Zero cost when disabled: no routes, no poller, guard exits before starting Python. |

## 3. Phase 1 — shipped

What shipped and why.

| Piece | What | Why |
|---|---|---|
| Mailbox on the hub | `a2a.db` tables `a2a_messages`, `a2a_audit`, `a2a_presence`, `a2a_state`; routes `/v1/a2a/{send,inbox,ack,agents,pause,resume,audit}` mounted only when enabled | Reuses the sync server, token and device ids; nothing new to deploy. |
| MCP tools | `send_message`, `inbox`, `reply`, `list_agents` | Works in every MCP client. |
| Delivery | Per-session MCP process long-polls; local `a2a_inbox`; hook notice (metadata only); Claude Code channel push via plugin; pi push via extension | Next-turn notice works everywhere; push adds liveness where available. |
| Peer turn | Row in `a2a_peer_turn` from when content enters context until Stop / `agent_end` / TTL (900 s) | The risk window is "peer text is in context", not "a reply was sent". |
| Lockdown | PreToolUse guard (exit 2, fails closed), MCP `peer_gate`, pi `a2a_check_tool` | One decision function, three enforcement points. |
| Quarantine | `remember()` during a peer turn goes to `quarantined_fragments`; approve via CLI | Keeps useful learnings without letting a peer write memory. |
| Limits | hops 3, 20 msgs/thread, 30 sends/min/device, 16 KiB, `msg_id` dedupe | Stops agent ping-pong loops and floods. |
| Ops | `hippo a2a pause|resume|audit|policy|quarantine|alias`; `hippo register claude-code --plugin` | See `docs/RUNBOOK.md`. |
| `claude` wrapper | Shell function appending `--channels plugin:hippocampus@hippocampus` | See decisions. |

Acceptance: unit tests (`tests/unit/test_a2a_*.py`) cover the store, guard
decisions and session-key parity, channel framing, quarantine, the pi bridge, the `claude` shell function (real zsh/bash) and
the hook notice. Live multi-device runs are tracked under Risks.

## 4. Decisions

| Decision | Choice | Reason |
|---|---|---|
| Transport | HTTP long-poll (`wait` up to 25 s) from the client to the hub | Works through NAT/Tailscale, no inbound ports, no websocket server; sqlite polling at 0.5 s is fine for a handful of agents. |
| Who holds the connection | The per-session MCP process, not a daemon | Already exists, already knows its session key and client; dies with the session. |
| Storage | Separate `a2a.db`, not the fragment DB | `collect_ops` reads fragments/tombstones/associations/projects only, so messages cannot sync; ephemeral data stays out of long-term memory. |
| Quarantine location | `a2a.db` on the device | Same reason: never synced until the owner approves. |
| Enforcement | PreToolUse hook with exit 2 | Documented by Anthropic to block before permission rules and under bypass modes. Read-only, stdlib-only, fails closed. |
| `act` / `block` / `answer` | Per-peer policy in config, default `answer` | Trust is a per-peer owner decision, not a per-message one. |
| Shell wrapper | Function, not alias | `--channels` is variadic; an alias prepends it and swallows the subcommand (`--channels entries must be tagged`). A function appends it last and bypasses subcommands. |
| Plugin MCP | `hippo register claude-code --plugin` removes the user-scope entry | Otherwise two servers (two pollers, duplicate tools). |

## 5. Non-Goals

- Cross-account messaging (phase 2).
- Interoperating with other A2A-protocol agents (phase 3).
- Guaranteed delivery to an idle, non-live session (phase 4).
- Real sender authentication inside one account: "verified sender" is the device
  id under the shared sync token.

## 6. Risks

| Risk | Mitigation / status |
|---|---|
| Prompt injection from a peer (or content it relayed) | Contained, not eliminated: lockdown, quarantine, send restriction, fencing, limits. A peer can still make an agent say something false. `act` peers are fully trusted. |
| Guard bug locks the user out | Fails closed on purpose; runbook has `hippo uninstall-hooks` and `a2a.db` removal. TTL clears stale turns. |
| Exit-2 deny under `bypassPermissions` | Documented by Anthropic, not tested here. |
| Org allowlist replaced | `allowedChannelPlugins` replaces Anthropic's default list; owners must re-add official channel plugins. |
| Idle-session wake on channel push | Not verified live. |
| pi end to end | Not verified live. |
| `claude` subcommand list drifts | The function hard-codes subcommands (from Claude Code 2.1.319); refresh when new ones ship. |
| Shared token = shared identity | Any device with the token can claim any address. Acceptable within one account; blocks phase 2 until per-peer tokens exist. |

## 7. Roadmap

| Phase | Scope |
|---|---|
| 2 | Cross-account: per-peer tokens and a trust list, so a sender identity is authenticated; per-pair policy and quotas. |
| 3 | A2A protocol facade: Agent Cards describing each address, JSON-RPC `message/send` and tasks on top of the mailbox, and an `a2a_call` client tool for synchronous request/response with a timeout. |
| 4 | Codex app-server adapter (push into Codex sessions) and headless resume for idle Claude Code / pi sessions, so a message can start work with nobody at the keyboard (needs its own safety review). |

Known follow-ups: `hippo doctor` reports `mcp:x` for Claude Code in plugin mode
(false positive); `hippo config set` prints every setting including
`sync_token` (mask secrets).
