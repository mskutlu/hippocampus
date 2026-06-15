# Hippocampus

> Shared biologically-inspired long-term **and** short-term memory for AI assistants.
> One backend, auto-injected into Devin, Claude Code, Cursor, Codex, OpenCode, Windsurf, Antigravity, VS Code Copilot, and Pi.

The human brain does not record everything — it synthesizes, distills, and leaves behind fragments.
Frequently accessed knowledge grows stronger; unused knowledge fades and is forgotten.
Meanwhile, working memory keeps the current task in focus.

Hippocampus implements both as an external memory substrate for AI assistants.

## Memory layers

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
- **Context isolation** — sessions are scoped by client plus terminal/workspace
  context, so two terminals using the same client do not share one ledger.
- **60-second dedup** — safe to log aggressively; duplicates merge.
- **Optional distillation** on `end_progress` turns the ledger into one
  long-term fragment.

### Transcript history — `log_transcript` / `get_transcript`

- **Raw prompts** captured by lifecycle hooks before the prompt is truncated
  into a one-line working-memory ask.
- **Visible assistant responses** can be stored when the client or AI calls
  `log_transcript(role="assistant", ...)`.
- **Reasoning summaries only** — hidden chain-of-thought is not stored; use
  `role="reasoning_summary"` for a concise visible summary of decisions.
- **Not injected by default** — transcript history is audit/provenance data,
  while fragments stay synthesized long-term memory.

### LLM Wiki — `hippo wiki` / `wiki_*`

- **Project-scoped wiki state in SQLite** — pages, sources, index, and log are
  callable directly from Hippocampus instead of discovered by scanning a repo.
- **Initialization gate** — ingest/query/lint/file-answer operations stop with
  `wiki_not_initialized` until the project runs `hippo wiki init`.
- **Markdown materialization** — exported `.md` files are generated views for
  Obsidian/git; the database remains canonical.
- **Source-backed pages** — ingested markdown/text sources create source pages,
  index/log entries, and queryable wiki context.

---

## Install

### Platform support

| Platform | CLI | MCP server | Web UI | Periodic jobs (decay / inject / archive) | Status |
|---|---|---|---|---|---|
| **macOS** | ✓ | ✓ | ✓ | `launchd` — installed automatically | Fully tested |
| **Linux** (incl. WSL2) | ✓ | ✓ | ✓ | `cron` — installer prints lines to add | Works, cron setup manual |
| **Windows (WSL2)** | ✓ | ✓ | ✓ | `cron` — installer prints lines to add | Same as Linux — **recommended Windows path** |
| **Windows native** (Git Bash / PowerShell) | ✓ | ✓ | ✓ | Task Scheduler — manual | Best-effort; some client paths differ |

If you're on Windows, **WSL2 is the recommended path**. Native Windows works for the Python bits but a few client config paths (e.g. Devin's `%APPDATA%\devin\config.json` vs `~/.config/devin/config.json`) may need manual adjustment.

### Prerequisites

| Requirement | Install |
|---|---|
| **Python 3.11+** | macOS: `brew install python@3.12` · Debian/Ubuntu: `sudo apt install python3.12 python3.12-venv` · Arch: `sudo pacman -S python` · Fedora: `sudo dnf install python3.12` · Windows (WSL): same as your distro · Windows native: [python.org](https://www.python.org/downloads/) |
| **uv** | All platforms: `curl -LsSf https://astral.sh/uv/install.sh \| sh` · Windows PowerShell: `irm https://astral.sh/uv/install.ps1 \| iex` |
| **git** | macOS: `xcode-select --install` · Linux: `sudo apt install git` / `sudo pacman -S git` / etc. · Windows: [git-scm.com](https://git-scm.com/download/win) or WSL distro |

### Clone and install

```bash
# macOS / Linux / WSL — single command works everywhere
git clone https://github.com/mskutlu/hippocampus.git
cd hippocampus
bash scripts/install.sh
```

```powershell
# Windows native (PowerShell) — Python/CLI only, periodic jobs are manual
git clone https://github.com/mskutlu/hippocampus.git
cd hippocampus
uv sync
uv pip install -e .
uv run hippo init
uv run hippo register
uv run hippo inject --commit
uv run hippo doctor
```

The installer auto-resolves its own repo location, so you can clone into `~/src/`, `~/code/`, `~/projects/`, `/opt/hippocampus`, or anywhere else. On first run it:

1. Runs `uv sync` and installs the `hippo` CLI into a repo-local `.venv/`.
2. Creates `~/.hippocampus/` for runtime state (DB, logs, backups, model cache).
3. Installs periodic jobs **on macOS only** (launchd agents: hourly decay, 10-minute inject, daily archive). On Linux / WSL it prints the `crontab -e` lines to paste. On Windows-native it points you at Task Scheduler.
4. Registers the Hippocampus MCP server in every detected AI client's config (Devin, Claude Code, Cursor, Codex, OpenCode, Windsurf, Antigravity, VS Code Copilot). For Cursor this is `~/.cursor/mcp.json`. For Pi — which deliberately ships without native MCP — it instead installs a bundled TypeScript extension at `~/.pi/agent/extensions/hippocampus/` that spawns the MCP server and re-exposes all 15 tools through `pi.registerTool()`.
5. Writes the first injection block into each client's rules file. For Cursor this is the always-on rule `~/.cursor/rules/hippocampus.mdc` (`alwaysApply: true`), for Codex `~/.codex/AGENTS.md`, for VS Code Copilot `~/.copilot/instructions/hippocampus.instructions.md`, for Pi `~/.pi/agent/AGENTS.md`. Every pre-existing file gets a one-time `<path>.pre-hippocampus.bak` copy before mutation.
6. Runs `hippo doctor`.

### Updating an existing install

You normally do **not** need to delete and reinstall Hippocampus after pulling
changes. Refresh the editable install, apply migrations, refresh client config,
refresh hooks, rebuild injection files, and then run the health check:

```bash
git pull
uv sync
uv pip install -e .
uv run hippo init
uv run hippo register
uv run hippo install-hooks
uv run hippo inject --commit
uv run hippo reindex
uv run hippo doctor
```

If you installed extras, refresh the same extras you use, for example
`uv pip install -e '.[semantic]'` or `uv pip install -e '.[semantic,web]'`.

What those commands update:

- `hippo init` applies new SQLite migrations, including session keys and
  transcript history.
- `hippo register` refreshes MCP config for Devin, Claude Code, Cursor, Codex,
  OpenCode, Windsurf, Antigravity, VS Code Copilot, and refreshes Pi's bundled
  extension.
- `hippo install-hooks` refreshes lifecycle hooks for Devin, Claude Code,
  Cursor, and Antigravity.
- `hippo inject --commit` refreshes the always-on rules files, including
  `~/.codex/AGENTS.md`.
- `hippo reindex` fills any missing embeddings after new auto-distilled
  fragments or migrations.

Restart any AI client after `register`, `install-hooks`, or `inject` so it
reloads MCP config and global instructions. Codex users do not need shell hooks,
but should restart Codex after updates so `~/.codex/config.toml` and
`~/.codex/AGENTS.md` are re-read.

### Linux / WSL — setting up cron

The installer will print these lines; paste them into `crontab -e` (or use `crontab -l | { cat; echo "..."; } | crontab -`):

```cron
# Hippocampus periodic jobs (paths are absolute — copy exactly from install output)
0 *    * * *  env HIPPOCAMPUS_HOME="$HOME/.hippocampus" HIPPOCAMPUS_VAULT="$HOME/hippocampus-vault" /path/to/hippo decay   >>"$HOME/.hippocampus/logs/cron-decay.log"   2>&1
*/10 * * * *  env HIPPOCAMPUS_HOME="$HOME/.hippocampus" HIPPOCAMPUS_VAULT="$HOME/hippocampus-vault" /path/to/hippo inject  >>"$HOME/.hippocampus/logs/cron-inject.log"  2>&1
15 4   * * *  env HIPPOCAMPUS_HOME="$HOME/.hippocampus" HIPPOCAMPUS_VAULT="$HOME/hippocampus-vault" /path/to/hippo archive >>"$HOME/.hippocampus/logs/cron-archive.log" 2>&1
```

If you prefer systemd timers instead of cron, the shape is: three `.service` units wrapping `hippo decay|inject|archive` plus three `.timer` units. A PR adding `scripts/systemd/` is welcome.

### Windows native — Task Scheduler

Open Task Scheduler and create three tasks pointing at the absolute path of `hippo.exe` (inside `.venv\Scripts\`):

| Task | Arguments | Trigger |
|---|---|---|
| `Hippocampus Decay` | `decay` | Every 1 hour |
| `Hippocampus Inject` | `inject` | Every 10 minutes |
| `Hippocampus Archive` | `archive` | Daily, 04:15 local |

For each task, set **Run whether user is logged on or not** and add `HIPPOCAMPUS_HOME` / `HIPPOCAMPUS_VAULT` environment variables under **Actions → Edit**.

### Optional extras

Run these from inside the cloned repo (any platform):

```bash
# Semantic recall (local ONNX embeddings, ~130 MB model download)
uv pip install -e '.[semantic]'
hippo reindex          # embed existing fragments
hippo recall "some query"

# Web dashboard at http://127.0.0.1:7878
uv pip install -e '.[web]'
hippo web

# Heavy embedders (BGE-large, mxbai-embed-large, e5-large, stella, …)
# Pulls sentence-transformers + torch (~2 GB).
# Apple Silicon → MPS; CUDA box → CUDA; else CPU.
uv pip install -e '.[heavy]'
hippo embeddings bench \
  --provider sentence-transformers \
  --models "BAAI/bge-small-en-v1.5,intfloat/e5-large-v2" \
  --queries my-queries.jsonl
```

### Auto-trigger in Devin + Claude Code + Cursor + Antigravity + Pi

```bash
hippo install-hooks     # registers Devin + Claude Code + Cursor + Antigravity shell hooks
hippo register          # installs the Pi extension (covers Pi's lifecycle)
```

Codex is covered by MCP registration plus the global `~/.codex/AGENTS.md`
injection file. It is intentionally not listed in this hook table because Codex
does not currently consume these shell lifecycle hooks; use `log_progress`,
`recall`, `remember`, `log_transcript`, and the other MCP tools directly inside a
Codex session.

| Hook | Devin | Claude Code | Cursor | Antigravity | Pi | What it does (v1.5+) |
|---|---|---|---|---|---|---|
| `SessionStart` / `sessionStart` / `session_start` | ✓ | ✓ | ✓ | ✓ | ✓ | Opens a Hippocampus session and injects the memory protocol **+ the live working ledger + top long-term fragments** as context. The AI sees real state from turn 0 instead of whatever the rules file happened to hold. On Cursor this uses the `sessionStart` hook's `additional_context` field — the one Cursor event that supports context injection. |
| `UserPromptSubmit` / `beforeSubmitPrompt` / `before_agent_start` | ✓ | ✓ | ⚠︎ | ✓ | ✓ | Logs the user prompt as `kind="ask"` AND (on Devin/Claude/Antigravity/Pi) re-injects the live working block + top fragments matching the prompt. **Cursor caveat:** its `beforeSubmitPrompt` output schema is `{continue, user_message}` only and cannot inject context, so on Cursor this hook is side-effect-only (logs the ask + runs autoremember). Per-turn memory in Cursor surfaces via the always-on `~/.cursor/rules/hippocampus.mdc` rule and the MCP `recall` tool instead. |
| `PostCompaction` | ✓ | — | — | — | — | Devin-only. Runs `hippo inject` to refresh the rules file, then injects the live context as `additionalContext`. Claude Code's `PostCompact` event doesn't accept `additionalContext` yet (community issues open); coverage on Claude Code comes from `UserPromptSubmit` instead. Cursor's `preCompact` is observational only; coverage comes from the always-on rule + next `sessionStart`. Pi's auto-compaction goes through the same `before_agent_start` re-injection on the very next turn. |

Pi's hooks are not shell scripts — they live inside the bundled TypeScript extension installed by `hippo register`. Running `hippo install-hooks` after that is harmless (it only touches Devin, Claude Code, Cursor, and Antigravity).

Before 1.5.0 the WORKING block was kept up-to-date on disk but the AI client only re-read the rules file at session start, so after a compaction the model was looking at a stale snapshot. Re-run `hippo install-hooks` after upgrading to pick up the new behaviour, and **restart your AI client** so it reloads its config.

Hook auto-install works on macOS, Linux, and WSL (the hooks are bash scripts). Native Windows users need to translate them into PowerShell or run Devin inside WSL.

Token cost is bounded: each hook payload is capped at `hook_inject_budget_chars` (default 3500 chars ≈ 800 tokens). Adjust or disable per layer with `hippo config set hook_inject_working false` / `hook_inject_fragments false` / `hook_fragment_limit 3` / `hook_inject_budget_chars 2000`.

### Cursor

`hippo register` adds Hippocampus to `~/.cursor/mcp.json` as an `mcpServers.hippocampus` entry tagged with `HIPPOCAMPUS_CLIENT=cursor`. `hippo inject` writes the long-term and working-memory marker blocks into `~/.cursor/rules/hippocampus.mdc`, an always-on project-rule file (`alwaysApply: true`) Cursor includes in every chat. `hippo install-hooks` wires `~/.cursor/hooks.json`:

- `sessionStart` — opens a session and injects the protocol + live ledger + top fragments via `additional_context`.
- `beforeSubmitPrompt` — side-effect-only (logs the ask, runs autoremember). Cursor's `beforeSubmitPrompt` cannot inject context, so per-turn recall comes from the always-on rule and the MCP `recall` tool.

After installing, **restart Cursor** so it reloads `mcp.json`, the rule file, and `hooks.json`. Recent Cursor versions load global `~/.cursor/rules/*.mdc` rules across projects; if your version doesn't pick it up, copy or symlink `~/.cursor/rules/hippocampus.mdc` into a project's `.cursor/rules/`, or rely on the `sessionStart` hook + MCP `recall` (both work regardless). The `hooks.json` integration is the officially-supported global path and always applies.

### OpenCode

`hippo register` adds Hippocampus to `~/.config/opencode/opencode.json` under
top-level `mcp.hippocampus`. OpenCode does **not** support top-level
`mcpServers`; older Hippocampus versions could write that Cursor-style key, and
current `hippo register` removes the legacy `mcpServers.hippocampus` entry if it
finds one.

The OpenCode entry uses its native local-server shape:

```json
{
  "mcp": {
    "hippocampus": {
      "type": "local",
      "enabled": true,
      "timeout": 30000,
      "command": ["hippocampus-mcp"],
      "environment": {
        "HIPPOCAMPUS_CLIENT": "opencode"
      }
    }
  }
}
```

### Codex

`hippo register` adds Hippocampus to `~/.codex/config.toml` as
`[mcp_servers.hippocampus]` and tags tool calls with
`HIPPOCAMPUS_CLIENT=codex`. `hippo inject` writes the long-term and
working-memory blocks into `~/.codex/AGENTS.md`, which Codex loads as global
instructions.

Codex does not currently use the shell lifecycle hooks installed by
`hippo install-hooks`. Keep the periodic `hippo inject` job enabled for on-disk
context refreshes, and use the MCP tools directly for live recall/progress
updates inside a Codex session:

- `log_progress` / `get_progress` / `end_progress` for working memory.
- `recall` / `remember` for long-term memory.
- `log_transcript` / `get_transcript` for raw prompt and visible assistant
  response history.

Because Codex can run multiple terminals or worktrees for the same repo,
Hippocampus scopes sessions below the client using terminal and workspace
context. Two Codex terminals can therefore keep separate working ledgers instead
of sharing one broad `codex` session.

After installing or upgrading, restart Codex so it reloads both
`~/.codex/config.toml` and `~/.codex/AGENTS.md`.

### Session identity

Hippocampus allows multiple active sessions for the same client. The stable
session key includes client plus terminal/workspace context, with terminal TTY as
a first-class discriminator and the current workspace path as supporting
context. This avoids mixing unrelated work when you run several Codex, Claude
Code, Devin, Cursor, or Pi sessions at once.

Empty sessions are cleaned up by `hippo cleanup-sessions`, and the normal health
flow reports session bloat through `hippo doctor` / `hippo health`.

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
OK  Cursor         long:✓ working:✓ mcp:✓
OK  Codex          long:✓ working:✓ mcp:✓
OK  OpenCode       long:✓ working:✓ mcp:✓
OK  Windsurf       long:✓ working:✓ mcp:✓
OK  Antigravity    long:✓ working:✓ mcp:✓
OK  VS Code Copilot long:✓ working:✓ mcp:✓
OK  Pi Agent       long:✓ working:✓ mcp:✓        # mcp:✓ here means "extension installed"
OK  launchd plist OK                              # macOS only
OK  settings: working_block_mode=per_client …
OK  embeddings: N/N covered (model=…, dim=…)      # only if [semantic] installed
OK  hooks/devin       SessionStart:✓ UserPromptSubmit:✓ PostCompaction:✓  # only if you ran hippo install-hooks
OK  hooks/claude-code SessionStart:✓ UserPromptSubmit:✓
OK  hooks/cursor      sessionStart:✓ beforeSubmitPrompt:✓
OK  hooks/antigravity SessionStart:✓ UserPromptSubmit:✓
OK  hooks/pi          session_start:✓ before_agent_start:✓ session_shutdown:✓  # via the bundled extension
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

# Working memory (hook-supported clients auto-log asks; Codex uses MCP calls directly)
hippo progress log goal     "Ship the feature"
hippo progress log done     "Wrote the migration"
hippo progress log decision "Use a single-writer consumer"
hippo progress show --client devin
hippo progress end --distill --summary "Shipped it"

# Transcript history
hippo transcript log user --stdin < prompt.txt
hippo transcript log assistant "Visible answer text"
hippo transcript show --client devin

# LLM Wiki (DB-backed, markdown export optional)
hippo wiki status --project hippocampus
hippo wiki init --project hippocampus --materialize
hippo wiki ingest raw/inbox/article.md --project hippocampus --materialize
hippo wiki query "what do we know about durable memory?" --project hippocampus
hippo wiki file-answer "Durable Memory Summary" --project hippocampus --stdin
hippo wiki lint --project hippocampus
hippo wiki export --project hippocampus

# Admin
hippo stats
hippo health --duplicates
hippo list --tag kafka
hippo decay --dry-run
hippo archive --dry-run
hippo cleanup-sessions --dry-run
hippo reconcile-mirror --dry-run
hippo inject --commit
```

Browse `hippo --help` and `hippo <subcommand> --help` for the full surface.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ AI Clients (via MCP stdio)                                   │
│  Devin · Claude Code · Cursor · Codex · OpenCode · Windsurf  │
│  · Antigravity · VS Code Copilot · Pi (via extension)        │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│ Hippocampus MCP Server (Python, 15 tools)                    │
│   long-term: recall · remember · forget · pin · unpin ·      │
│              get_fragment · list_fragments · top_fragments · │
│              get_stats                                       │
│   working:   log_progress · get_progress · end_progress ·    │
│              undo_last_entry                                 │
│   transcript: log_transcript · get_transcript                 │
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
│   ~/.cursor/rules/hippocampus.mdc                            │
│   ~/.codex/AGENTS.md                                         │
│   ~/.config/opencode/AGENTS.md                               │
│   ~/.codeium/windsurf/memories/global_rules.md               │
│   ~/.antigravity/rules/global_rules.md                       │
│   ~/.copilot/instructions/hippocampus.instructions.md        │
│   ~/.pi/agent/AGENTS.md                                      │
│                                                              │
│ Each file is backed up once to <path>.pre-hippocampus.bak.   │
│                                                              │
│ Pi additionally gets a TypeScript extension at:              │
│   ~/.pi/agent/extensions/hippocampus/index.ts                │
│ which spawns the MCP server and re-exposes its 15 tools      │
│ through Pi's native pi.registerTool() API + lifecycle hooks. │
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
- `plans/v8/` — compaction-safe context re-injection + sqlite-vec roadmap
- `plans/v10/` — DB-backed LLM Wiki layer
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

[MIT](./LICENSE) © msk

## Contributing

PRs welcome. Run `uv run pytest tests -q` before pushing; the suite should
stay green (131/131 at last count).
