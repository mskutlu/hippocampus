# PRD V9 — Passive autonomy + biology recalibration

> Status: drafted
> Author: @msk
> Target version: `1.6.0`

## 1. Problem

Audit of the live DB on 2026-05-18 (186 fragments / 309 sessions / 2,671 ledger entries) revealed seven structural issues that defeat the system's goal of "remember without telling things by itself":

1. **Decay outpaces boost ~16:1** over the last 7 days (7193 decays vs 450 boosts). Math: a fragment needs to be touched once every 7.5 days just to stay flat. Anything less → guaranteed decay.
2. **Zombie fragments**: high-access, near-zero-confidence rows. Top examples:
   - 22× access, conf=0.00 — "Vault seeder"
   - 20× access, conf=0.01 — "docs Neo4j graph"
   - 17× access, conf=0.00 — "ERP error-code translation"

   These were referenced 13–22 times yet decayed to floor because the +0.015 boost can't outrun the −0.002 per cycle (24 cycles/day on launchd).

3. **Sessions never end**: 265/309 (86%) still `ended_at IS NULL`. `auto_end_idle_minutes` setting exists but defaults to `None` (disabled). The auto-distill code path is unreachable in default deployments.

4. **Distillation rate is 12.9%** (31 of 241 sessions that had real work were ever turned into a fragment). 210 sessions' worth of asks/dones/decisions live only in the ledger.

5. **Tag fragmentation**: 368 of 567 (65%) tags are singletons — date-specific, task-specific names the AI invented once and never reused. Recall via tag is broken.

6. **No autonomous remembering**: the user repeatedly says "anaconda network problem, add it to global rules so I don't have to tell it every time", but the AI never `remember()`s these without being told. The phrases "always do X", "never do Y", "remember", "add to rules" should trigger automatic durable writes.

7. **No autonomous correction**: only 4 `forget()` calls have happened in the lifetime of the system. When the user pushes back ("no", "wrong", "actually"), nothing decays the cited fragment.

## 2. Goals

| ID | Goal |
|----|------|
| G1 | Decay only fragments NOT accessed in the last `decay_skip_recent_days` (default 30). Zombies die only after real disuse. |
| G2 | Default `auto_end_idle_minutes = 60` and have `auto_end_idle_sessions()` auto-distill any closed session with ≥3 entries to a fragment. |
| G3 | Auto-pin fragments after `auto_pin_access_threshold` (default 10) accesses, so heavily-used knowledge can't be decayed to zero. |
| G4 | On every `log_progress`, semantically recall and boost the top-K fragments matching the entry's content (small boost), not only the explicitly-cited `frag_...` ids. |
| G5 | The `UserPromptSubmit` hook detects autonomy-trigger phrases (`\b(remember|always|never|don't forget|add to (global )?rules|next time|from now on)\b`) and auto-`remember()`s the surrounding context. |
| G6 | Tag canonicalization on insert: a new tag with high string/semantic similarity to an existing tag (default ≥ 0.85) is replaced with the canonical existing tag. |
| G7 | Negation-detection: when the user's next prompt after the AI cited a fragment contains a strong negation phrase, auto-`forget()` that fragment with reason="user_negation_inferred". |
| G8 | Predictive recall: extend `hook_context` to use the current session's last ask AND last 3 dones as additional query streams for the long-term fragment recall in the `UserPromptSubmit` / `PostCompaction` payload. |
| G9 | Cluster-level boost: when `boost_many` fires, propagate a smaller boost (default `BOOST_DELTA / 3`) to each fragment's first-degree neighbors in `associations`. |
| G10 | New `hippo dedup` CLI: find near-duplicate fragments by cosine similarity (default ≥ 0.95), display side-by-side, offer merge. |
| G11 | External observation pipeline: `hippo observe` reads JSONL lines from `~/.hippocampus/observations.jsonl` and creates fragments with `source_type="auto-observed"`. Auto-observed fragments start at confidence 0.30 (lower than manual 0.50) and decay normally unless the AI boosts them within N days. |
| G12 | All new biology is settings-driven (env or `config.json`), defaults safe. Reversible. |

## 3. Non-goals

- Reranker integration (V10).
- Migrate to `sqlite-vec` (V10; still doesn't matter at <50k fragments).
- Touch the UI / vault mirror format.

## 4. Design

### 4.1 Settings — new keys (all in `config._DEFAULTS`)

| Key | Default | Used by |
|-----|---------|---------|
| `auto_end_idle_minutes` | `60` (was `None`) | `auto_end_idle_sessions` |
| `auto_distill_min_entries` | `3` | session distillation on idle |
| `auto_pin_access_threshold` | `10` | auto-pin promotion |
| `decay_skip_recent_days` | `30` | decay loop |
| `log_progress_recall_boost_k` | `3` | A5 |
| `log_progress_recall_min_score` | `0.50` | A5 |
| `cluster_boost_factor` | `0.33` | C2 |
| `tag_canonicalize_threshold` | `0.85` | B3 |
| `dedup_cosine_threshold` | `0.95` | C3 |
| `autoremember_enabled` | `True` | B2 |
| `autoremember_min_chars` | `60` | B2 (skip too-short prompts) |
| `inferred_negation_enabled` | `True` | B4 |
| `inferred_negation_window_turns` | `2` | B4 |
| `observe_default_confidence` | `0.30` | C4 |

### 4.2 Module changes

| File | What changes |
|------|--------------|
| `dynamics/decay.py` | Add `decay_skip_recent_days` skip; pinned + recent-access shield logic |
| `dynamics/boost.py` | Auto-pin on access threshold; cluster propagation in `boost_many` |
| `dynamics/autoremember.py` | NEW — phrase detector + `auto_remember_from_prompt()` |
| `dynamics/negation.py` | NEW — recent-cite tracker + `infer_negation()` |
| `mcp/tools.py` | `log_progress` now calls `_auto_boost_recall` (A5); `auto_end_idle_sessions` now distills (G2); `recall` records cited fragments for negation inference |
| `storage/fragments.py` | `create` calls `canonicalize_tags()` before insert |
| `storage/tag_canonical.py` | NEW — fuzzy + semantic tag merging |
| `embeddings/dedup.py` | NEW — near-duplicate detection |
| `clients/hook_context.py` | Extend `render_context` with `extra_query_streams` (last ask + last 3 dones) |
| `clients/registry.py` | No change |
| `cli/main.py` | `hippo dedup`, `hippo observe`, `hippo autoremember test "<text>"` |
| `scripts/hooks/user-prompt-submit.sh.template` | Call `hippo autoremember --client X` against the prompt |

### 4.3 Decay rewrite

```python
# pseudo
shield = sessions.accessed_fragment_ids_in_sessions(last_n_session_ids(2))
recent_cutoff_iso = now - decay_skip_recent_days

for frag in fragments:
    if frag.pinned:
        skip ("pinned")
    if frag.id in shield:
        skip ("session-shield")
    if frag.last_accessed_at and frag.last_accessed_at >= recent_cutoff_iso:
        skip ("recent-access")           # NEW
    decay normally
```

Result: a fragment accessed once last week is shielded from decay for ~3 more weeks. Stops the 16:1 ratio.

### 4.4 Auto-pin on access threshold

In `boost()`, after updating fields, check:
```python
if not frag.pinned and frag.accessed + 1 >= config.get_setting("auto_pin_access_threshold"):
    frag_store.update_fields(frag_id, pinned=True)
    feedback.log(frag_id, "auto-pin", reason="access-threshold")
```

### 4.5 log_progress recall + boost (A5 + C2)

In `log_progress()`, after the explicit-id boost loop:
```python
if entry and entry.content:
    hits = recall(query=entry.content, limit=get_setting("log_progress_recall_boost_k"))
    for h in hits.fragments:
        if h.scores.semantic >= get_setting("log_progress_recall_min_score"):
            boost(h.id, context_tag=f"log_progress:{kind}", session_id, client)
            # C2: cluster boost
            for nb in associations.neighbors(h.id):
                small_boost(nb, factor=get_setting("cluster_boost_factor"))
```

### 4.6 Auto-distill on idle (G2)

`auto_end_idle_sessions()` becomes:
```python
for sid, client in idle_sessions(idle_minutes):
    entries = ledger.current_entries(sid)
    if len(entries) >= get_setting("auto_distill_min_entries"):
        content = render_ledger_as_fragment(entries, explicit_summary=None)
        frag_store.create(
            content=content,
            summary=_derive_summary(entries),
            tags=["session-summary", client, "auto-distilled"],
            source_type="session-summary",
            source_ref=sid,
        )
    sessions.close_session(sid)
    sessions.open_session(client)
    _refresh_working_block(client)
```

### 4.7 Pattern-detect autoremember (B2)

`hippo autoremember --client X` reads stdin (the user prompt), detects trigger phrases via regex + windowed extraction:

```python
TRIGGER_RE = re.compile(
    r"\b("
    r"remember(?: this| that)?|"
    r"always|"
    r"never|"
    r"don'?t forget|"
    r"from now on|"
    r"next time|"
    r"add (?:this|it) to (?:the )?(?:global )?rules|"
    r"keep in mind"
    r")\b",
    re.IGNORECASE,
)
```

If a trigger appears, capture the sentence containing it + 1 sentence before/after (the rule body) and call `remember()`. Tags include `auto-remembered`, `client:<X>`, and the trigger word that fired.

### 4.8 Tag canonicalization (B3)

`canonicalize_tags(new_tags: list[str]) -> list[str]`:
1. For each new tag, compute `difflib.SequenceMatcher` ratio against every existing tag.
2. If ratio ≥ `tag_canonicalize_threshold`, replace with existing tag.
3. Otherwise check semantic similarity (cosine on the embedding of each).
4. If both miss, keep the new tag.

### 4.9 Negation inference (B4)

Two pieces of state in `storage/cited.py`:
- `recent_cited_in_session(session_id, limit=5) → list[fragment_id]` — derived from feedback_log rows of kind=`boost` with reason starting `log_progress:` since session start.
- On every new ledger ask, run `negation.infer(text)` — regex over `\b(no|wrong|actually|that'?s? not|hayır|yanlış)\b`. If true AND `inferred_negation_enabled`, find the AI's last boosted fragment in this session and apply `forget()` with reason=`user_negation_inferred`.

### 4.10 hippo dedup (C3)

```
hippo dedup [--threshold 0.95] [--limit 20] [--merge frag_a frag_b]
```
- Loads all embeddings into memory.
- Pairwise cosine; emits pairs ≥ threshold with side-by-side display.
- `--merge` keeps the higher-confidence row's id; copies tags from the loser; appends loser content to the keeper's content if not already substring; deletes loser row.

### 4.11 hippo observe (C4)

JSONL line format (the user / scripts append to `~/.hippocampus/observations.jsonl`):
```json
{"content":"git commit on acme-orders: …","summary":"…","tags":["git","acme-orders"],"source_ref":"commit:abc123"}
```

`hippo observe` reads new lines (tracked via offset file), calls `remember()` with `source_type="auto-observed"`, default confidence 0.30. Designed for shell hooks / git pre-commit hooks the user can wire up later.

## 5. Tests

| Test file | Coverage |
|-----------|----------|
| `tests/unit/test_decay_recency_skip.py` | Recent-access skip; pinned still skipped; sessions shield still works |
| `tests/unit/test_auto_pin.py` | Crosses threshold → pinned + feedback row |
| `tests/unit/test_autoremember.py` | Each trigger phrase fires; "no trigger" no-op; min-chars guard |
| `tests/unit/test_tag_canonical.py` | difflib match → replaced; semantic match → replaced; novel tag preserved |
| `tests/unit/test_negation.py` | Negation regex; window respected; disabled flag |
| `tests/unit/test_dedup.py` | Near-duplicates flagged; identical pairs deduped; non-duplicates ignored |
| `tests/integration/test_auto_distill.py` | Idle session with entries → fragment created; idle session with no entries → just closed |
| `tests/integration/test_log_progress_recall_boost.py` | log_progress boosts semantically-matched fragments |
| `tests/integration/test_observe.py` | observations.jsonl file → fragments created |

Existing 88 tests must remain green.

## 6. Documentation

- `CHANGELOG.md` `[1.6.0]` entry per goal
- `README.md` "Autonomy" section
- `docs/ARCHITECTURE.md` — extend §"Biological model" with the recency-skip rule
- `docs/RUNBOOK.md` — operator commands for `hippo dedup`, `hippo observe`, `hippo autoremember`

## 7. Migration

All changes ship with safe defaults. No schema changes (or single optional column if needed). Existing fragments / sessions / ledger entries are unaffected. Users can opt out per-feature via `hippo config set <key> false`.
