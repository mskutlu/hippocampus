---
date: 2026-04-20
status: draft
version: 1.4.0
owner: anon
---

# PRD — Hippocampus V1.4: Auto-trigger via Lifecycle Hooks

## 1. Problem

Today the memory protocol lives inside the rules-file marker block. The AI
should follow it, but doesn't always — it skims past the block or relies on
the user to paste a reminder like
`"Use the Hippocampus memory protocol …"` at the start of each new session.

Pasting anything is a non-starter for real use.

## 2. Solution

Wire Hippocampus into Devin-for-Terminal's **lifecycle hooks**
(https://cli.devin.ai/docs/extensibility/hooks/lifecycle-hooks) — the same
hooks format Claude Code uses, so Claude Code gets it too.

Two hooks do the work:

### `SessionStart`
Fires once when a Devin / Claude Code session begins. It:
1. Opens (or rotates) a Hippocampus session for the client.
2. Emits `additionalContext` reminding the AI about the memory protocol +
   the current top-N fragments + the current working ledger. This shows up
   in the AI's context as if injected, no user typing needed.

### `UserPromptSubmit`
Fires on every user message. It:
1. Calls `hippo progress log ask "<user message>" --client <name>` so the
   ask appears in the working-memory block automatically — the AI no
   longer has to remember to call `log_progress(kind="ask")`.
2. Optionally emits `additionalContext` with a terse per-turn reminder.

Both hooks are **user-global** so they apply to every project.

## 3. Functional Requirements

1. **FR-1** Two shell scripts installed under
   `~/.config/devin/hippocampus-hooks/`: `session-start.sh` and
   `user-prompt-submit.sh`. Each is idempotent, tolerates `hippo` being
   missing, and returns `{}` on any internal error (never blocks the user).
2. **FR-2** Scripts use only `bash`, `python3`, `jq`-free parsing (pipe to
   `python3 -c '...'`).
3. **FR-3** Scripts write structured JSON to stdout in the format
   accepted by Devin / Claude Code hooks (`{"hookSpecificOutput": {...}}`
   with `additionalContext` where supported; fallback to top-level
   `additionalContext`).
4. **FR-4** A new CLI `hippo install-hooks` that registers both hooks in:
   - `~/.config/devin/config.json` under `"hooks"`
   - `~/.claude/settings.json` under `"hooks"`
   - Creates timestamped `.bak` copies before mutation.
5. **FR-5** A new CLI `hippo uninstall-hooks` that removes the Hippocampus
   hook entries (leaves other hooks alone) and restores no .bak is needed.
6. **FR-6** `hippo doctor` reports whether hooks are installed for each
   client.
7. **FR-7** The scripts **must not block** if Hippocampus is broken
   (missing DB, crashed MCP server, etc.) — the worst case is that the
   context injection is skipped. A normal chat turn must never be delayed
   by more than 500 ms by the hooks.

## 4. Non-Goals

- **N-1** Windsurf / OpenCode / Antigravity — those clients don't document
  the same hooks format yet. For them we stick with rules-file injection.
- **N-2** Hook-level tool-call interception. We only inject context; we
  don't try to intercept tool calls.
- **N-3** Project-level hooks (`.devin/hooks.v1.json`). User-global only
  for V1.4.

## 5. Technical Considerations

- Hook scripts should be ≤ 50 ms typical latency. They run once per
  message for `UserPromptSubmit`, so a slow script degrades chat
  responsiveness.
- Use `--client devin` / `--client claude-code` based on a parent-env
  hint if available; fall back to script-name disambiguation.
- JSON output schema (Claude Code-compatible):
  ```json
  {
    "hookSpecificOutput": {
      "hookEventName": "SessionStart",
      "additionalContext": "..."
    }
  }
  ```
- Exit code 0 on success; any non-zero is logged and treated as no-op.

## 6. Success

- **M-1** Starting a fresh `devin` session with no user prompt shows the
  Hippocampus protocol injected as context on turn 0.
- **M-2** First user message produces one `ask` entry in the working
  ledger without the AI calling any tool.
- **M-3** `hippo doctor` reports hooks:✓ for Devin and Claude Code.
- **M-4** Hook script mean latency ≤ 50 ms.
