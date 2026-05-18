# V9 — Passive autonomy + biology recalibration — task list

PRD: [prd-passive-autonomy.md](./prd-passive-autonomy.md)

## Wave 1 — Foundation (settings, decay rewrite, auto-pin)

- [ ] W1-1 Add new settings keys to `config._DEFAULTS` with safe defaults
- [ ] W1-2 `dynamics/decay.py` — add `decay_skip_recent_days` skip
- [ ] W1-3 `dynamics/boost.py` — auto-pin when `accessed` crosses threshold
- [ ] W1-4 Tests: `test_decay_recency_skip.py`, `test_auto_pin.py`

## Wave 2 — Session lifecycle

- [ ] W2-1 `mcp/tools.auto_end_idle_sessions` — auto-distill on close (G2/B1)
- [ ] W2-2 Test: `test_auto_distill.py` (integration)
- [ ] W2-3 Default `auto_end_idle_minutes = 60` confirmed

## Wave 3 — Pattern-detect autoremember (the headline feature)

- [ ] W3-1 `dynamics/autoremember.py` — phrase detector + `auto_remember_from_prompt()`
- [ ] W3-2 `mcp/tools.py` — expose `auto_remember(prompt, client)` as a tool
- [ ] W3-3 `cli/main.py` — `hippo autoremember <prompt>` (reads stdin if `-`)
- [ ] W3-4 `scripts/hooks/user-prompt-submit.sh.template` — call autoremember
- [ ] W3-5 Test: `test_autoremember.py`

## Wave 4 — log_progress recall boost + cluster propagation

- [ ] W4-1 `mcp/tools.log_progress` — after explicit-id boost, recall + boost top-K
- [ ] W4-2 `dynamics/boost.boost_many` — propagate to neighbors via associations
- [ ] W4-3 Test: `test_log_progress_recall_boost.py`

## Wave 5 — Tag canonicalization

- [ ] W5-1 `storage/tag_canonical.py` — difflib + semantic match
- [ ] W5-2 `storage/fragments.create` — canonicalize tags before insert
- [ ] W5-3 Test: `test_tag_canonical.py`

## Wave 6 — Negation inference

- [ ] W6-1 `dynamics/negation.py` — regex + recent-cite tracker
- [ ] W6-2 `mcp/tools.log_progress` — call `infer_negation` on kind="ask"
- [ ] W6-3 Test: `test_negation.py`

## Wave 7 — Predictive recall (extend hook payload)

- [ ] W7-1 `clients/hook_context.render_context` — support `extra_query_streams`
- [ ] W7-2 `cli/main.context` — wire ledger as extra streams when `--client` has session
- [ ] W7-3 Update unit tests in `test_hook_context.py`

## Wave 8 — hippo dedup CLI

- [ ] W8-1 `embeddings/dedup.py` — pairwise cosine
- [ ] W8-2 `cli/main.dedup` — `--threshold --limit --merge`
- [ ] W8-3 Test: `test_dedup.py`

## Wave 9 — hippo observe CLI

- [ ] W9-1 `cli/main.observe` — read JSONL, persist offset, create fragments
- [ ] W9-2 Test: `test_observe.py`

## Wave 10 — Docs + release

- [ ] W10-1 CHANGELOG `[1.6.0]`
- [ ] W10-2 README "Autonomy" section
- [ ] W10-3 ARCHITECTURE update
- [ ] W10-4 RUNBOOK commands
- [ ] W10-5 Bump `pyproject.toml` version
- [ ] W10-6 Push to origin

## Done definition

- All 9 waves' tests pass
- `pytest tests -q` green (existing 88 + new ones)
- Manual: `hippo autoremember "remember to always run hippo doctor"` creates a fragment
- Manual: idle a session 60+ min → fragment with tags=["session-summary","client","auto-distilled"] appears
- Manual: `hippo dedup --threshold 0.95` lists 0+ candidates without crashing
