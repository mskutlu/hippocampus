# Hippocampus

> Shared biologically-inspired long-term **and** short-term memory for AI assistants.
> One backend, auto-injected into Devin, Claude Code, OpenCode, Windsurf, and Antigravity.

The human brain does not record everything — it synthesizes, distills, and leaves behind fragments.
Frequently accessed knowledge grows stronger; unused knowledge fades and is forgotten.
Meanwhile, working memory keeps the current task in focus.

Hippocampus implements both as an external memory substrate for AI assistants.

## Two memory layers

### Long-term memory — `recall` / `remember` / `forget` / `pin`

- **Synthesized fragments**, never raw conversations.
- **Confidence boost on access** (`+0.015`) — frequently used knowledge strengthens.
- **Session decay when unused** (`-0.002`) — obsolete knowledge fades.
- **Shield** — recently-used fragments don't decay.
- **Pin** — critical fragments never decay.
- **Associations** — fragments returned together become linked.
- **Negative feedback** (`-0.02`) — the AI flags wrong memories; they decay faster.
- **No time-based decay** — change only on access, feedback, or explicit cycles.
- **Top-N auto-injected** — every client's rules file always carries the
  highest-ranking fragments so the LLM sees them without calling a tool.

### Working memory — `log_progress` / `get_progress` / `end_progress`

- **Per-session ledger** of asks / dones / decisions / blockers / next steps.
- **Updated every turn** — the AI calls `log_progress` reflexively; the block
  is regenerated immediately so the next turn sees the new entry.
- **Survives compaction** because the WORKING block lives in the always-on
  rules file that every client re-injects after summarization.
- **Per-client isolation** — Devin has its own ledger, Claude Code has its own.
- **60-second dedup** — safe to log aggressively; duplicates merge.
- **Optional distillation** on `end_progress` turns the ledger into one
  long-term fragment.

## Quickstart

```bash
# 1. Install
cd ~/IdeaProjects/hippocampus
bash scripts/install.sh

# 2. Optional extras for semantic recall + web UI
uv pip install -e '.[semantic,web]'
hippo reindex          # embed existing fragments
hippo web              # start the local dashboard at http://127.0.0.1:7878

# 3. Verify
hippo doctor     # expect: long:✓ working:✓ mcp:✓ for every client
                 #         embeddings: N/N covered
                 #         settings: working_block_mode=per_client ...

# 4. Long-term memory
hippo remember -c "Kafka retries need idempotent consumers." -s "kafka idempotency" -t kafka
hippo recall "kafka"           # hybrid FTS + semantic
hippo top --limit 10

# 5. Working memory — use during an actual task
hippo progress log goal     "Ship V1.2 web UI"
hippo progress log ask      "User asked about semantic recall"
hippo progress log done     "Wrote migration 003"
hippo progress log decision "Use fastembed for local embeddings"
hippo progress show

# 6. Session wrap — optionally distill to long-term
hippo progress end --distill --summary "Shipped V1.2 web UI"
```

The AI clients will see both blocks automatically in their next session.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ AI Clients (via MCP stdio)                                   │
│  Devin · Claude Code · OpenCode · Windsurf · Antigravity     │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│ Hippocampus MCP Server (Python, 12 tools)                    │
│   long-term: recall, remember, forget, pin, unpin,           │
│              get_fragment, list_fragments, top_fragments,    │
│              get_stats                                       │
│   working:   log_progress, get_progress, end_progress        │
└───────────┬──────────────────────────────┬───────────────────┘
            │                              │
            ▼                              ▼
┌──────────────────────────┐   ┌───────────────────────────────┐
│ SQLite (canonical)       │ ←→│ Obsidian mirror (markdown)    │
│ ~/.hippocampus/          │   │ ~/hippocampus-vault/Fragments/*.md  │
│   hippocampus.db         │   │   .archive/*.md               │
└───────────┬──────────────┘   └───────────────────────────────┘
            ▲
            │ periodic jobs via launchd
            │   decay    (every 1 hour)
            │   inject   (every 10 minutes)
            │   archive  (every 24 hours)
            │
┌───────────┴──────────────────────────────────────────────────┐
│ hippo CLI                                                    │
└──────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────┐
│ Each client's global rules file carries TWO always-on blocks:│
│                                                              │
│   <!-- HIPPOCAMPUS:START -->                                 │
│     top-N fragments (confidence × recency)                   │
│   <!-- HIPPOCAMPUS:END -->                                   │
│                                                              │
│   <!-- HIPPOCAMPUS:WORKING:START -->                         │
│     current session ledger (goal / asks / dones / ...)       │
│   <!-- HIPPOCAMPUS:WORKING:END -->                           │
│                                                              │
│ Files:                                                       │
│   ~/.config/devin/AGENTS.md                                  │
│   ~/.claude/CLAUDE.md                                        │
│   ~/.config/opencode/AGENTS.md                               │
│   ~/.codeium/windsurf/memories/global_rules.md               │
│   ~/.antigravity/rules/global_rules.md                       │
│                                                              │
│ Each file is backed up once to <path>.pre-hippocampus.bak.   │
└──────────────────────────────────────────────────────────────┘
```

## Design docs

- `plans/v1/prd-hippocampus-v1.md` — long-term memory PRD
- `plans/v1/tasks-hippocampus-v1.md` — V1 task breakdown
- `plans/v2/prd-working-memory.md` — working-memory PRD
- `plans/v2/tasks-working-memory.md` — V0.2 task breakdown
- `docs/ARCHITECTURE.md` — data flow, schema, injection pipeline, 12-tool surface
- `docs/RUNBOOK.md` — operations, backup/restore, debugging, working-memory recipes
- `CHANGELOG.md` — versioned changes

## Uninstall

```bash
bash scripts/uninstall.sh           # removes launchd + marker blocks + MCP registrations
rm -rf ~/.hippocampus               # also drop the DB + logs
rm -rf ~/hippocampus-vault/Fragments      # and the Obsidian mirror
```

Every rules file we touched was backed up to `<path>.pre-hippocampus.bak` on
first mutation, so the original state is always recoverable.
