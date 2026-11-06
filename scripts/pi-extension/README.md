# Hippocampus extension for Pi

This directory is the **template** for the Pi extension that Hippocampus installs
into `~/.pi/agent/extensions/hippocampus/` whenever you run `hippo register`.

It does three things from inside Pi (`@earendil-works/pi-coding-agent`):

1. **Re-exposes all 13 Hippocampus MCP tools as native Pi tools.** Pi doesn't
   ship with built-in MCP — capabilities are added via TypeScript extensions —
   so this extension spawns `hippocampus-mcp` as a singleton child process and
   forwards every tool call (recall, remember, log_progress, …) over JSON-RPC.
2. **Wires Pi's lifecycle to Hippocampus:**
   - `session_start` opens a working session for client `pi`.
   - `before_agent_start` logs the user's ask, runs autoremember on the full
     prompt, and appends the live working ledger + top long-term fragments
     to the system prompt. This is the compaction-safety mechanism.
   - `session_shutdown` tears down the MCP child cleanly.
3. **Adds a `/hippocampus` slash command** that runs `hippo doctor` and surfaces
   the result via the Pi notification banner.

4. **Receives A2A messages and locks tools during peer turns** (only when
   `a2a_enabled` is on; see "Agent-to-agent messaging (A2A)" in the main README).
   - *Live push:* the MCP child runs with `HIPPOCAMPUS_CHANNEL=1` and emits
     `notifications/claude/channel`. `a2a-frame.ts` parses it and the extension
     injects it with `pi.sendUserMessage` as `Message from agent <addr> ...
     UNTRUSTED DATA` (`deliverAs: "steer"` while the agent is streaming). Peer
     prompts are not logged or auto-remembered as user asks.
   - *Tool lockdown:* peer content (a push, or `inbox()` returning messages)
     starts a peer turn. While it is active every `tool_call` is checked through
     the hidden MCP method `a2a_check_tool`, which uses the same decision as the
     Claude Code guard: project-local `read`/`grep`(single file)/`find`/`ls` and
     the hippocampus read tools pass; `bash`, `edit`, `write` and anything else
     are blocked. Fail closed: if the check errors, the tool is blocked.
   - *End of turn:* `agent_end` calls `a2a_end_turn`; if that call fails the lock
     stays until the TTL (`a2a_peer_turn_ttl_s`, 900 s).
   - Per-peer `act`/`block` policy and `remember()` quarantine apply as on every
     client (`hippo a2a policy ...`, `hippo a2a quarantine ...`).

## Install / uninstall

Don't copy this directory manually — let the installer do it so the
`__HIPPO_BIN__`, `__HIPPOCAMPUS_MCP__`, and `__HIPPOCAMPUS_CLIENT__` placeholders
get rendered with absolute paths:

```bash
hippo register                  # installs (or refreshes) the extension
hippo unregister                # removes only the files we installed
```

Run `hippo doctor` afterwards to confirm Pi shows up as `long:ok working:ok mcp:ok`
(the `mcp` badge here means "extension installed", not a JSON entry in any
config file — Pi has no MCP config).

After install, restart Pi (or run `/reload` inside it) so the extension is
loaded with the freshly-rendered paths.

## Files

| File | Purpose |
|------|---------|
| `index.ts` | Extension entry point. Renders the bridge + lifecycle hooks + A2A lockdown. |
| `a2a-frame.ts` | Pure helpers: parse a channel notification, frame it as an untrusted peer prompt. |
| `README.md` | This file. Copied into the install dir for reference. |

## Verify A2A by hand

Needs the hub set up (README) and `a2a_enabled true` on the device; re-run
`hippo register`, then restart Pi or `/reload`.

1. From another agent or device, send this Pi session a message
   (`send_message` to `pi@<device>`). Pi should show a `Message from agent ...
   UNTRUSTED DATA` prompt without you typing anything. If Pi was idle this is
   the unverified case (it should start a turn).
2. During that turn ask Pi to run `bash` or `edit` a file: it must be blocked
   with `A2A peer-turn restriction`. Reading a project file with `read` works;
   reading `~/.ssh/config` is blocked.
3. Ask Pi to `remember` something: the result says `quarantined`; check
   `hippo a2a quarantine list`.
4. After the turn ends, tools work again. `hippo a2a policy set pi@<device> block`
   then resend: nothing is delivered.

End-to-end behavior in Pi has not been verified live yet.
