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

## Install / uninstall

Don't copy this directory manually — let the installer do it so the
`__HIPPO_BIN__`, `__HIPPOCAMPUS_MCP__`, and `__HIPPOCAMPUS_CLIENT__` placeholders
get rendered with absolute paths:

```bash
hippo register                  # installs (or refreshes) the extension
hippo unregister                # removes only the files we installed
```

Run `hippo doctor` afterwards to confirm Pi shows up as `long:✓ working:✓ mcp:✓`
(the `mcp` badge here means "extension installed", not a JSON entry in any
config file — Pi has no MCP config).

After install, restart Pi (or run `/reload` inside it) so the extension is
loaded with the freshly-rendered paths.

## Files

| File | Purpose |
|------|---------|
| `index.ts` | Extension entry point. Renders the bridge + lifecycle hooks. |
| `README.md` | This file. Copied into the install dir for reference. |
