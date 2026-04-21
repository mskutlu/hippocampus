# Runbook

## Install

```bash
cd ~/IdeaProjects/hippocampus
bash scripts/install.sh
```

The installer is idempotent — rerun it any time. It will:

1. `uv sync` + `uv pip install -e .` so `hippo` and `hippocampus-mcp` land on `PATH`
2. Create `~/.hippocampus/` and run migrations
3. Render launchd plists and `launchctl bootstrap` them
4. Register the Hippocampus MCP server in every client's config
5. Write the first injection block into every client's rules file
6. Run `hippo doctor`

## Uninstall

```bash
bash scripts/uninstall.sh
```

Removes launchd agents, unregisters MCP from every client, strips the marker
block from every rules file. Leaves your data in `~/.hippocampus/` and your
vault mirror untouched.

To nuke the data as well:

```bash
rm -rf ~/.hippocampus
rm -rf ~/hippocampus-vault/Fragments
```

## Working-memory ledger (V0.2)

Working memory lives in a second always-on block (`HIPPOCAMPUS:WORKING`) next
to the long-term block. It holds the current session's asks, dones,
decisions, blockers — regenerated on every `log_progress` call so it's
compaction-proof.

### From an AI client

The AI should call these tools reflexively:

```
log_progress(kind="ask",      content="<one-line paraphrase of user ask>")
log_progress(kind="done",     content="<what you just completed>")
log_progress(kind="decision", content="<decision just made>")
log_progress(kind="blocker",  content="<what's blocking>")
log_progress(kind="next",     content="<next step planned>")
log_progress(kind="goal",     content="<the task goal, when set/changed>")
log_progress(kind="note",     content="<any other context>")

get_progress()                              # read full ledger
end_progress(distill_to_fragment=True,      # close and optionally distill
             summary="Session summary")
```

### From the CLI

```bash
hippo progress log ask "Write a PRD for the working memory feature"
hippo progress log done "Shipped migration 002 + 3 MCP tools + 14 tests"
hippo progress log decision "Dedup window = 60s"
hippo progress show
hippo progress show --client devin
hippo progress show --full                  # all entries (default is last 50)
hippo progress end --distill --summary "..."  # close + distill to long-term
hippo progress clear --confirm              # drop current ledger (admin)
```

### Session semantics

- Each AI client maintains its own session pointer (`~/.hippocampus/sessions/<client>.id`).
- A session starts lazily on the first `log_progress` call.
- `end_progress` rotates the session: old entries stay in SQLite (preserved,
  never deleted) but stop appearing in the rendered WORKING block.
- Sessions older than 24 hours are auto-closed.

### What's in the block

```
## Hippocampus — current session
<protocol instructions>

**Session**: sess_... · client: devin · started: HH:MM UTC · turn: N

### Goal
- [HH:MM] The goal line

### Asks (latest)
- [HH:MM] First ask
...

### Completed
### Blockers (open)
### Decisions
### Next
### Notes
```

Block rendering is bounded (~3 KB). Full history is always queryable via
`get_progress(full=True)`.

## Daily operations

```bash
hippo stats                 # dashboard
hippo top --limit 20        # what's currently highest-ranking
hippo recall "some query"   # search + boost
hippo remember -c "..."     # store a new fragment (or pipe to stdin)
hippo list --tag kafka      # admin listing, no boost
hippo pin   frag_01H...     # shield a fragment
hippo unpin frag_01H...
hippo forget frag_01H...    # negative feedback (-0.02)
hippo inject --dry-run      # preview next injection block
hippo doctor                # full health check
```

## Daemon control

```bash
launchctl print gui/$(id -u)/com.hippocampus.daemon
launchctl kickstart -k gui/$(id -u)/com.hippocampus.daemon   # run once now

# stop / start / restart
launchctl bootout   gui/$(id -u)/com.hippocampus.daemon
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hippocampus.daemon.plist
```

Logs:

```
~/.hippocampus/logs/daemon-decay.out.log    # decay cycles
~/.hippocampus/logs/daemon-decay.err.log
~/.hippocampus/logs/daemon-inject.out.log   # injection runs
~/.hippocampus/logs/daemon-archive.out.log
~/.hippocampus/logs/mcp-YYYY-MM-DD.log      # MCP tool calls
```

## Backup / restore

Everything state-ful lives in two places:

- `~/.hippocampus/hippocampus.db` (canonical)
- `~/hippocampus-vault/Fragments/*.md` (human-readable mirror)

Back both up — they are consistent on their own but the mirror is the
friendlier recovery source if SQLite is ever corrupted.

```bash
# Quick snapshot
cp ~/.hippocampus/hippocampus.db ~/.hippocampus/backups/manual-$(date +%Y%m%d).db

# Restore from a snapshot
bash scripts/uninstall.sh
cp ~/.hippocampus/backups/manual-20260101.db ~/.hippocampus/hippocampus.db
bash scripts/install.sh
```

If the mirror and DB disagree, trust the DB — regenerate the mirror:

```bash
uv run --project ~/IdeaProjects/hippocampus python -c "
from hippocampus.sync import obsidian_mirror
obsidian_mirror.bootstrap_hooks()
from hippocampus.storage import fragments as F
for f in F.iter_all():
    obsidian_mirror.write_fragment(f.to_dict())
print('regenerated', F.count(), 'mirror files')
"
```

## Debugging

### "My confidences look weird"

```bash
hippo stats
tail -20 ~/.hippocampus/logs/daemon-decay.out.log
sqlite3 ~/.hippocampus/hippocampus.db \
  "SELECT kind, COUNT(*), SUM(delta) FROM feedback_log GROUP BY kind;"
```

### "AI isn't seeing a fragment I just stored"

```bash
hippo inject --commit          # force-refresh all client blocks now
cat ~/.hippocampus/_HIPPOCAMPUS_CONTEXT.md
```

Top-N ranking = `confidence × 0.7 + recency × 0.3`. A brand-new fragment
starts at confidence 0.5 with `last_accessed_at=NULL` (recency=0), so it
scores ~0.35. If your store already has highly-boosted content you may need
to pin the new fragment or call `recall` on it to boost it above the
injection cutoff.

### "A client seems to be ignoring the block"

```bash
hippo doctor
```

Walks through every client and checks both the rules file and the MCP config.
Yellow `WARN` lines show you where to look. Typical causes:

- Client hasn't been restarted since install — restart it
- MCP server isn't registered in the client's MCP config file
- The client uses a non-default rules path (override in
  `src/hippocampus/clients/registry.py`)

### "I want to undo the injection block in a client's rules file"

Each rules file was backed up once to `*.pre-hippocampus.bak`:

```bash
cp ~/.config/devin/AGENTS.md.pre-hippocampus.bak ~/.config/devin/AGENTS.md
```

Or use the dedicated CLI:

```bash
hippo strip-blocks
```

### "MCP server isn't starting"

MCP stdio is finicky about stdout pollution. Hippocampus never prints to
stdout — but if you've edited the code, double-check that no `print()` calls
leaked in. All logs go to stderr and `~/.hippocampus/logs/mcp-*.log`.

```bash
hippocampus-mcp < /dev/null                # quick syntax check
tail ~/.hippocampus/logs/mcp-*.log
```

## Safety invariants

- Pinned fragments never decay, never auto-archive.
- `hippo decay --dry-run` and `hippo archive --dry-run` preview changes.
- Rules files are backed up once on first Hippocampus write.
- MCP config files are backed up on every write (timestamped `.bak`).
- Archive only moves mirror files — never permanently deletes data.
