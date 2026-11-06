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

## LLM Wiki operations

The wiki layer is database-backed. Markdown files are optional exports. Always
initialize a project before ingesting, querying, linting, or filing answers.

```bash
hippo wiki status --project hippocampus
hippo wiki init --project hippocampus --materialize
hippo wiki ingest raw/inbox/article.md --project hippocampus --dry-run
hippo wiki ingest raw/inbox/article.md --project hippocampus --materialize
hippo wiki query "what does the wiki say about memory?" --project hippocampus
hippo wiki file-answer "Memory Summary" --project hippocampus --stdin --source-id wsrc_01H...
hippo wiki lint --project hippocampus
hippo wiki export --project hippocampus
```

If a project is missing, commands return `wiki_not_initialized`. Do not create
ad hoc `index.md` or `log.md` files while continuing the operation. Run
`hippo wiki init` first, then retry.

SQLite is canonical for wiki pages, sources, index, and log. Use
`hippo wiki export` to regenerate markdown files when the exported view drifts.
Queries use SQLite FTS and return each page's source IDs plus source records,
including the original `source_ref`.

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

Back both up. The CLI uses SQLite's online backup API, verifies integrity, and
retains the newest 14 snapshots by default.

```bash
hippo backup
hippo backup --retention 30

hippo restore ~/.hippocampus/backups/hippocampus-20260101T120000000000Z.db
```

Restore first creates a verified `pre-restore` safety snapshot. Migrations do
the same with a `pre-migration` snapshot and restore it automatically if an
upgrade fails.

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


## Device sync (V11)

### Server

- Install on the always-on host: `bash scripts/install.sh --sync-server`.
  The token is stored as `sync_server_token` in that host's
  `~/.hippocampus/config.json` (mode 0600); `hippo sync token --show` prints it.
- The oplog is `~/.hippocampus/sync.db` (override with `HIPPOCAMPUS_SYNC_DB`).
  It is append-only; back it up like any SQLite file. There is no compaction
  in V11: a full re-enrollment replays every op, which is fine at fragment
  volumes.
- Health: `curl http://<host>:7879/v1/health` (no auth) returns `head_seq`
  and the number of devices seen.
- Logs: `~/.hippocampus/logs/sync-server.*.log` (launchd) or
  `journalctl --user -u hippocampus-sync` (systemd).

### Enrolling a device

1. `hippo config set sync_url http://<host>:7879`, `hippo config set sync_token <token>`.
2. If the device already has memory: `hippo purge-noise --commit` and
   `hippo project backfill --commit`.
3. `hippo sync` by hand. The first push sends every fragment (one op each);
   the first pull replays the server log. Both run in 200-op batches.
4. `hippo config set sync_enabled true`; from now on `hippo inject` syncs.
5. `hippo doctor` must show `sync: ... last_ok=<time> pending=0`.

Enroll the device with the richest memory first so its history is the base
everyone else pulls.

### Recovering from a bad merge

1. `hippo restore ~/.hippocampus/backups/<file>.db` on the affected device
   (`hippo backup` runs before every purge and migration).
2. Reset that device's sync cursors so it replays the server log against the
   restored database:
   ```bash
   sqlite3 ~/.hippocampus/hippocampus.db "DELETE FROM sync_state WHERE key IN ('last_pull_seq','last_push_watermark')"
   hippo sync
   ```
   Replaying is idempotent: ops already applied merge to no change.
3. If the server log itself is wrong, stop the server, move `sync.db` aside,
   start it again, and re-enroll devices from the healthiest one.

### What is never synced

Sessions, ledgers, handoffs, transcripts, wiki tables, backups, and the
embedding model cache. These are device-local by design.


## Agent-to-agent messaging (A2A)

Off by default. Concepts and setup are in the README ("Agent-to-agent messaging
(A2A)"); design in `docs/ARCHITECTURE.md`. Hub state is `a2a.db` next to the
oplog (`~/.hippocampus/a2a.db`, override `HIPPOCAMPUS_A2A_DB`); every device
also has its own `~/.hippocampus/a2a.db` (local inbox, peer turns, quarantine).

### Enable

1. Hub: `git pull`, `hippo config set a2a_enabled true >/dev/null`, then
   **restart `hippo sync serve`**. The `/v1/a2a/*` routes mount only at startup.
2. Check: `curl -H "Authorization: Bearer $(hippo sync token --show)" http://<host>:7879/v1/a2a/agents`
   returns `{"agents": [...]}`; a 404 means the flag is off or the server was not restarted.
3. Each device: `hippo config set a2a_enabled true >/dev/null`, install the
   plugin / hooks / alias per the README. `hippo doctor | grep hooks/claude-code`
   must show `PreToolUse:ok Stop:ok`.

`hippo config set` prints every setting including `sync_token`: always redirect
it to `/dev/null`.

### Disable

- Hub: `hippo config set a2a_enabled false >/dev/null`, restart `hippo sync serve`
  (routes disappear). Devices: same setting; MCP processes and the guard stop
  polling/denying once they re-read config (new session).
- Emergency stop without a restart: `hippo a2a pause` (hub rejects sends with
  423 and delivers nothing). `hippo a2a resume` undoes it.

### Restart the hub

```bash
# systemd (Linux)           systemctl --user restart hippocampus-sync
# launchd (macOS)           launchctl kickstart -k gui/$(id -u)/com.hippocampus.sync-server
# manual                    stop it, then: hippo sync serve --host <ip> --port 7879
```

Undelivered messages survive a restart (they are rows in `a2a.db`).

### Pause / resume

```bash
hippo a2a pause      # {"paused": true}
hippo a2a resume     # {"paused": false}
```

### Inspect the audit log

```bash
hippo a2a audit --limit 100        # hub: send, deliver, ack, reject (with reason), pause, resume
hippo a2a audit --local            # this device: quarantine events
```

`reject` rows carry the status and reason (`429 rate limit`, `409 max hops
exceeded`, `409 thread message cap reached`, `413`, `423 a2a paused`).

### Review the memory quarantine

`remember()` calls made while a session was answering a peer wait here.

```bash
hippo a2a quarantine list
hippo a2a quarantine approve <id>   # stores it through remember (it syncs from now on)
hippo a2a quarantine reject <id>
```

Read the content and the sender before approving: it was written by an agent
that had untrusted text in context.

### Change a peer's policy

```bash
hippo a2a policy list
hippo a2a policy set claude-code@laptop act        # any session on that device; trusted, no tool lockdown
hippo a2a policy set pi@desktop/<session> block    # one session; never delivered or shown
hippo a2a policy set claude-code@laptop answer     # back to the default
```

Policy is stored per device in config (`a2a_peer_policy`), so set it on the
device that receives the messages.

### Recover from a guard fail-closed

Symptom: every tool call in Claude Code is denied with `A2A guard failed` or
`A2A peer-turn restriction: guard error`. The guard denies when it cannot decide
while a2a is on.

1. Read the denial reason; `echo '{}' | hippo a2a hook pretooluse` reproduces it.
2. Fast unblock: `hippo uninstall-hooks` (then `hippo install-hooks` once fixed), or
   `hippo config set a2a_enabled false >/dev/null`, or delete the device's
   `~/.hippocampus/a2a.db` (drops undelivered local copies and any quarantine).
3. A stale peer turn clears itself after `a2a_peer_turn_ttl_s` (900 s) or at the
   next Stop.

### Update the shell function on all devices

When the function changes (for example a new `claude` subcommand to pass
through), on every device:

```bash
git pull && hippo a2a alias --install && source ~/.zshrc   # or open a new shell
```

`--install` replaces the marked block (`# >>> hippocampus a2a >>>`), so it is
idempotent. `hippo a2a alias --uninstall` removes it.

### Rotate the sync token

The A2A hub uses the sync bearer token.

1. On the hub: `hippo sync token` (generates and stores a new token; `--show` prints the current one), restart `hippo sync serve` (it reads the token at startup).
2. On every device: `hippo config set sync_token <new> >/dev/null`.
3. Check `hippo sync status` and `hippo a2a audit --limit 5` on each device.

Devices with the old token get 401 until updated, and their A2A pollers log
`a2a poll failed` and retry every 5 s.
