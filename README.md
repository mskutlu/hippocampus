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

---

## Install

### Prerequisites

| Requirement | Why | Install |
|---|---|---|
| **macOS** | The decay / inject / archive daemons use `launchd`. CLI and MCP server work on Linux and Windows too, but the timed background jobs need manual cron/systemd setup. | — |
| **Python 3.11+** | Runtime. | macOS: `brew install python@3.12` |
| **uv** | Package manager used by the install script. | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **git** | To clone. | macOS: `xcode-select --install` |

### Clone and install

```bash
# Clone anywhere you like — the install script resolves its own repo path.
git clone https://github.com/mskutlu/hippocampus.git
cd hippocampus

# Install (idempotent — safe to re-run)
bash scripts/install.sh
```

The installer:
1. Runs `uv sync` to create `.venv/` inside the repo and installs the `hippo` CLI there.
2. Creates `~/.hippocampus/` for runtime state (DB, logs, backups, model cache).
3. Installs `launchd` agents: hourly decay, 10-minute inject, daily archive.
4. Registers the Hippocampus MCP server in every detected AI client's config
   (Devin, Claude Code, OpenCode, Windsurf, Antigravity).
5. Writes the first injection block into each client's rules file. Before
   mutating any pre-existing file, it drops a one-time
   `<path>.pre-hippocampus.bak` copy so your original state is recoverable.
6. Runs `hippo doctor` and reports status.

Add `~/.hippocampus/bin` or the cloned repo's `.venv/bin` to your `PATH` if
you want to invoke `hippo` from anywhere (or use `uv run hippo …` from
inside the repo).

### Optional extras

Run these from inside the cloned repo:

```bash
# Semantic recall (local ONNX embeddings, ~130 MB model download)
uv pip install -e '.[semantic]'
hippo reindex          # embed existing fragments
hippo recall "some query"

# Web dashboard at http://127.0.0.1:7878
uv pip install -e '.[web]'
hippo web

# Heavy embedders (BGE-large, mxbai-embed-large, e5-large, stella, …)
# Pulls sentence-transformers + torch (~2 GB). MPS auto-detected on Apple Silicon.
uv pip install -e '.[heavy]'
hippo embeddings bench \
  --provider sentence-transformers \
  --models "BAAI/bge-small-en-v1.5,intfloat/e5-large-v2" \
  --queries my-queries.jsonl
```

### Auto-trigger in Devin + Claude Code

```bash
hippo install-hooks     # registers SessionStart + UserPromptSubmit hooks
```

This is the difference between "the AI might use memory if prompted" and
"the AI sees the protocol on turn 0 and every user message is auto-logged
as an `ask` before the AI even reads it." After installing hooks, **restart
your AI client** so it reloads its config.

### Verify

```bash
hippo doctor
```

Expected output:

```
OK  SQLite OK … fragments
OK  Vault mirror OK …
OK  Injection file OK …
OK  Devin CLI      long:✓ working:✓ mcp:✓
OK  Claude Code    long:✓ working:✓ mcp:✓
OK  OpenCode       long:✓ working:✓ mcp:✓
OK  Windsurf       long:✓ working:✓ mcp:✓
OK  Antigravity    long:✓ working:✓ mcp:✓
OK  launchd plist OK
OK  settings: working_block_mode=per_client …
OK  embeddings: N/N covered (model=…, dim=…)    # only if [semantic] installed
OK  hooks/devin       SessionStart:✓ UserPromptSubmit:✓   # only if you ran hippo install-hooks
OK  hooks/claude-code SessionStart:✓ UserPromptSubmit:✓
```

---

## Usage

```bash
# Long-term memory
hippo remember -c "Kafka retries need idempotent consumers." -s "kafka idempotency" -t kafka
hippo recall "kafka"
hippo top --limit 10
hippo pin   frag_01H...
hippo forget frag_01H...

# Working memory (if hooks installed, asks are auto-logged — you only do dones/decisions manually)
hippo progress log goal     "Ship the feature"
hippo progress log done     "Wrote the migration"
hippo progress log decision "Use a single-writer consumer"
hippo progress show --client devin
hippo progress end --distill --summary "Shipped it"

# Admin
hippo stats
hippo list --tag kafka
hippo decay --dry-run
hippo archive --dry-run
hippo inject --commit
```

Browse `hippo --help` and `hippo <subcommand> --help` for the full surface.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ AI Clients (via MCP stdio)                                   │
│  Devin · Claude Code · OpenCode · Windsurf · Antigravity     │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│ Hippocampus MCP Server (Python, 13 tools)                    │
│   long-term: recall · remember · forget · pin · unpin ·      │
│              get_fragment · list_fragments · top_fragments · │
│              get_stats                                       │
│   working:   log_progress · get_progress · end_progress ·    │
│              undo_last_entry                                 │
└───────────┬──────────────────────────────┬───────────────────┘
            │                              │
            ▼                              ▼
┌──────────────────────────┐   ┌───────────────────────────────┐
│ SQLite (canonical)       │ ←→│ Obsidian mirror (markdown)    │
│ ~/.hippocampus/          │   │ ~/hippocampus-vault/Fragments/│
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

---

## Design docs

- `plans/v1/` — long-term memory foundation (PRD + tasks)
- `plans/v2/` — working-memory ledger
- `plans/v3/` — working-memory iterations (shared block, auto-tag, undo, idle auto-end)
- `plans/v4/` — semantic recall (fastembed + hybrid)
- `plans/v5/` — web UI
- `plans/v6/` — sentence-transformers provider + bench
- `plans/v7/` — auto-trigger via lifecycle hooks
- `docs/ARCHITECTURE.md` — data flow, schema, injection pipeline
- `docs/RUNBOOK.md` — operations, backup/restore, debugging
- `CHANGELOG.md` — versioned changes

---

## Uninstall

```bash
# From inside the cloned repo:
bash scripts/uninstall.sh        # removes launchd agents, MCP registrations, and marker blocks

# Also drop your data (irreversible — consider backing up ~/.hippocampus first):
rm -rf ~/.hippocampus
rm -rf ~/hippocampus-vault/Fragments
```

Every rules file we touched was backed up once to `<path>.pre-hippocampus.bak`
on first mutation. Restore any of them with `cp <path>.pre-hippocampus.bak <path>`
if you ever want to revert to the original state.

---

## License

Not yet specified — add one before using publicly.

## Contributing

PRs welcome. Run `uv run pytest tests -q` before pushing; the suite should
stay green (79/79 at last count).
