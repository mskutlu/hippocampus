# V8 — Compaction-safe context re-injection — task list

PRD: [prd-compaction-fix.md](./prd-compaction-fix.md)

## A — Rendering core

- [ ] A1 `src/hippocampus/clients/hook_context.py`: new module with `render_context()`
- [ ] A2 Unit tests `tests/unit/test_hook_context.py`

## B — CLI surface

- [ ] B1 `hippo context --client X [--query Q] [...]` in `cli/main.py`
- [ ] B2 Unit tests via Click runner

## C — Hook scripts

- [ ] C1 `scripts/hooks/session-start.sh.template` — append live ledger + fragments to `additionalContext`
- [ ] C2 `scripts/hooks/user-prompt-submit.sh.template` — log ask AND emit context payload
- [ ] C3 `scripts/hooks/post-compaction.sh.template` — Devin-only; refresh disk + emit payload using compaction summary

## D — Installer

- [ ] D1 `clients/hooks.py`: render the new `post-compaction.sh` script
- [ ] D2 Register `PostCompaction` in Devin config only
- [ ] D3 `_strip_hooks` covers the new event
- [ ] D4 `status()` reports `PostCompaction` per-client

## E — Settings

- [ ] E1 New keys `hook_inject_working`, `hook_inject_fragments`, `hook_inject_budget_chars`, `hook_fragment_limit`
- [ ] E2 Honour them in `render_context()`

## F — Tests

- [ ] F1 Update `tests/integration/test_auto_hooks.py` for PostCompaction install + idempotency
- [ ] F2 Integration test: SessionStart script run with fake stdin → JSON contains additionalContext including WORKING text
- [ ] F3 Integration test: UserPromptSubmit script run with fake stdin → ask logged AND additionalContext present
- [ ] F4 Integration test: PostCompaction script run with fake stdin → `hippo inject` invoked AND additionalContext present

## G — Docs + release

- [ ] G1 `CHANGELOG.md` `[1.5.0]` entry
- [ ] G2 `README.md` hooks section refresh
- [ ] G3 `docs/ARCHITECTURE.md` injection vs hook subsection
- [ ] G4 v9 placeholder for `sqlite-vec` follow-up

## Done definition

- 4 hook events install cleanly on Devin (`SessionStart`, `UserPromptSubmit`,
  `PostCompaction`) and 2 on Claude Code (`SessionStart`, `UserPromptSubmit`).
- `pytest tests -q` green.
- Manually verified: open Devin → call `log_progress` → close → reopen
  Devin → first AI turn sees the freshly-logged entries in
  additionalContext, not a stale snapshot.
