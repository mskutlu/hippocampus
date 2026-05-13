# PRD V8 — Compaction-safe context re-injection + vector roadmap

> Status: drafted
> Author: @msk
> Target version: `1.5.0`

## 1. Problem

The V1.4 hook installer (`hippo install-hooks`) puts a `SessionStart` and a
`UserPromptSubmit` entry into each client's config. The current implementation
has two real gaps that defeat the design goal "Survives compaction" stated in
V0.2 PRD §G4:

1. **The rules file is read only at session start.** Every AI client (Devin,
   Claude Code) loads `~/.config/devin/AGENTS.md` / `~/.claude/CLAUDE.md` once
   when the session opens; the file is materialised into the system prompt
   and not re-read afterwards. The `WORKING` block IS being refreshed on every
   `log_progress` call — we just verified by reading the file and comparing
   it against the AI's actual system prompt mid-session — but the AI sees the
   snapshot it received on turn 0.

2. **`UserPromptSubmit` returns `{}`.** The current template logs the user
   prompt as `kind="ask"` and returns an empty JSON object. There is no
   `additionalContext`. So even though the on-disk WORKING block is now
   correct, nothing forces it into the model's context.

The user-visible symptom is exactly what the V0.2 PRD wanted to avoid:
after a compaction (or even on a fresh session), the AI thinks it is still
working on the *previous* task — because the WORKING block embedded in its
frozen system prompt belongs to the previous session.

## 2. Goals

| ID | Goal |
|----|------|
| G1 | After a compaction, the very next user message places the **current** WORKING block into the model's context as `additionalContext`. |
| G2 | A new session sees the live ledger (current asks/dones/decisions/blockers) from turn 0, not the previous session's snapshot. |
| G3 | Devin sessions automatically run `hippo inject` after `PostCompaction` so the disk-side block matches reality before the next `UserPromptSubmit` reads it. |
| G4 | Hooks ALSO pull the top-N long-term fragments matching the latest ask / compaction summary, so recall happens without the AI explicitly asking. |
| G5 | All extra injection is bounded — under ~4 KB per hook invocation — so token cost is predictable. |
| G6 | Backwards compatible: existing installations keep working after `hippo install-hooks` is re-run. |

## 3. Non-goals

- We do NOT try to extend Claude Code's `PreCompact` / `PostCompact` hooks
  (community issues #46191, #50682, #24965, #13170 are still open and
  `additionalContext` is not accepted there yet). Claude Code coverage comes
  via `UserPromptSubmit` and `SessionStart` only.
- We do NOT change the on-disk block format or marker pair. Existing rules
  files keep working unchanged.

## 4. Design

### 4.1 New helper: `clients/hook_context.py`

A single module that renders the "context payload" hooks should emit.
Re-uses `format_working_block` and `ranking.top_n`.

```python
def render_context(
    *,
    client: str,
    query: str | None = None,
    include_working: bool = True,
    include_fragments: bool = True,
    fragment_limit: int = 5,
    char_budget: int = 3500,
) -> str:
    """Plain-text payload for hookSpecificOutput.additionalContext."""
```

Output is plain markdown (no HTML markers — they're only used as file
landmarks). Always starts with a one-line provenance header so the AI knows
where the text came from. Truncates to `char_budget` to stay under the 10 KB
limit Claude Code applies to hook output.

### 4.2 New CLI: `hippo context`

```
hippo context --client devin [--query "<text>"] [--no-fragments] [--no-working] [--budget 3500]
```

Emits the rendered context to stdout. Used by every hook template.

### 4.3 Hook updates

| Hook | Old behaviour | New behaviour |
|------|---------------|---------------|
| `SessionStart` | static protocol text only | protocol text **+ live ledger + top fragments** |
| `UserPromptSubmit` | `{}` (no context) | `{additionalContext: <WORKING block + top fragments matching the prompt>}` |
| `PostCompaction` (Devin only) | not installed | runs `hippo inject` then emits same context payload using the compaction summary as the recall query |

For all three, the script is the same shape:
1. Optional side effect (open session / log ask / refresh injection file).
2. Call `hippo context …` to render the payload.
3. Emit `{"hookSpecificOutput": {"hookEventName": "<event>", "additionalContext": "<payload>"}}`.

### 4.4 Installer changes (`clients/hooks.py`)

- Render a third script `post-compaction.sh` per client.
- Register `PostCompaction` in `~/.config/devin/config.json` only.
- Leave Claude Code untouched for `PostCompaction` because additionalContext
  isn't supported there yet — the `UserPromptSubmit` re-injection is the
  active fix for Claude Code.

### 4.5 Configuration

New persisted settings (with defaults that match today's behaviour
post-fix):

| Key | Default | Purpose |
|-----|---------|---------|
| `hook_inject_working` | `true` | Whether `UserPromptSubmit` should re-inject the WORKING block. Off = old behaviour. |
| `hook_inject_fragments` | `true` | Whether to include top-N fragments in hook payloads. |
| `hook_inject_budget_chars` | `3500` | Hard cap per hook invocation. |
| `hook_fragment_limit` | `5` | Max long-term fragments per payload. |

All overridable via `hippo config set <key> <value>` and `HIPPO_<KEY>` env.

### 4.6 Token-cost model

Worst case (every user message): ~1 KB WORKING block + ~1.5 KB for 5
fragments ≈ 600–800 tokens added per turn. On a 200K context that is 0.3%
overhead. On a 32K context, 2.4%. Tunable via settings.

## 5. Vector storage roadmap (separate, follow-up)

This PRD ships with one config-flag-controlled experiment but does not
flip the default. Tracked as a follow-up under `plans/v9/`.

### 5.1 Current state

Already shipped in V1.1:
- `fragment_embeddings(fragment_id, vector BLOB, dim, model)` SQLite table.
- `fastembed` provider, default `BAAI/bge-small-en-v1.5` (384-dim).
- Pure-Python linear scan cosine in `embeddings/search.semantic_topk`.
- Hybrid blend with FTS5 in `mcp.tools.recall`: `score = fts*(1-w) + sem*w`.
- Optional `[heavy]` extra with `sentence-transformers` + bench command.

### 5.2 Sizing reality

A typical personal corpus is <10k fragments. At 384-dim that's 15 MB of
vectors and ~50 ms per linear scan. The current V1.3 bench shows all
tested models hit 100% hit@1 on the existing corpus, i.e. the bottleneck
is not retrieval, it's the size of the fragment store.

### 5.3 Worth-it scoreboard

| Upgrade | Effort | Quality lift | When to do it |
|---------|--------|--------------|---------------|
| **`sqlite-vec` extension** for KNN | M | 10× faster at scale; quality unchanged | Corpus > 50k or query latency > 200 ms |
| **Cross-encoder reranker** (`BAAI/bge-reranker-base`) | M | +5–15% nDCG | Anytime corpus > ~50 fragments |
| **Switch to a stronger embedder** (bge-large / e5-large) | S | +3–8% on MTEB | After bench shows hit@5 drop on real corpus |
| **External vector DB** (qdrant / chroma / pgvector) | L | None at this scale | Never, for a personal tool |

### 5.4 Plan

Document the sqlite-vec migration path so we can flip it on when needed,
without committing to ship it in 1.5.0:

1. Add an internal `embeddings.backend` ABC with `topk()` and `upsert()`.
2. Keep `LinearScanBackend` as the default.
3. Add `SqliteVecBackend` behind `embedding_backend = "sqlite_vec"` setting.
4. `hippo embeddings backend bench` to compare the two on the user's corpus.
5. Flip default to `sqlite_vec` only when the bench shows clear win at the
   user's actual scale.

V8 only writes this plan; the code change lands in V9 once we have
measured the cost at the user's actual corpus size.

## 6. Tests

- Unit tests for `render_context()` covering: budget enforcement,
  missing-session graceful degradation, "no provider" path, frag-limit cap.
- Unit tests for the new `hippo context` CLI: client filter, query
  optional, budget enforcement, JSON-safe stdout.
- Integration tests for the three updated hook scripts: each script
  invoked with realistic stdin, asserts the emitted JSON has the right
  `hookEventName` and a non-empty `additionalContext`.
- Installer test: `install_all()` must now also register `PostCompaction`
  in Devin's config, and the previous-version idempotency test still
  passes.

## 7. Documentation

- `CHANGELOG.md` — `[1.5.0]` entry.
- `README.md` — update the "Auto-trigger" section to mention PostCompaction
  and the new re-injection behaviour. Note that the WORKING block now
  ALSO arrives via hook output, not just the rules file.
- `docs/ARCHITECTURE.md` — add an "Injection vs hook" subsection
  clarifying which mechanism wins after compaction.

## 8. Migration / Compatibility

Re-running `hippo install-hooks` is the only step. Old script files are
overwritten with the new versions (idempotent by tag). Users who never
ran `install-hooks` keep the V1.3 behaviour (no automatic compaction
recovery).

## 9. Open questions

- Should `hook_inject_fragments=true` always recall, or only when the
  WORKING block has at least one ask? **Decision: always recall** — the
  cost is small and the AI benefits even when starting a brand-new
  task.
- Should we also re-inject after `Stop` events? **Decision: no** — Stop is
  the AI's choice and is rare; adds complexity for little gain.
