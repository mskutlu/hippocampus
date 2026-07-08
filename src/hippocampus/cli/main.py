"""`hippo` CLI entry point.

Subcommands:
    init           First-time setup (create dirs, run migrations, print next steps)
    doctor         Health check — DB, vault mirror, launchd, per-client injection
    session        start | end | status
    remember       Store a fragment (content from flag or stdin)
    recall         FTS search + boost
    forget         Apply negative feedback
    pin / unpin    Shield/unshield from decay
    stats          Print dashboard
    list           List fragments (no boost)
    top            Print top-N by rank
    decay          Run one decay cycle (dry-run supported)
    archive        Run archive sweep (dry-run supported)
    inject         Regenerate top-N file + upsert into all clients
    register       Register MCP server in all clients' configs
    unregister     Remove MCP server from all clients' configs
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import click

from hippocampus import config

OK_MARK = "ok"
MISS_MARK = "x"


# ---------------------------------------------------------------------------
# CLI root
# ---------------------------------------------------------------------------


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="hippocampus")
def cli() -> None:
    """Hippocampus — shared long-term memory for AI assistants."""


# ---------------------------------------------------------------------------
# init / doctor
# ---------------------------------------------------------------------------


def _bootstrap() -> None:
    config.ensure_dirs()
    from hippocampus.storage import db as sdb
    from hippocampus.sync import obsidian_mirror

    sdb.init_db()
    obsidian_mirror.bootstrap_hooks()


@cli.command()
def init() -> None:
    """Create runtime dirs, initialise the DB, install mirror hooks."""
    _bootstrap()
    click.echo(f"{OK_MARK} Hippocampus home: {config.HIPPOCAMPUS_HOME}")
    click.echo(f"{OK_MARK} DB:               {config.DB_PATH}")
    click.echo(f"{OK_MARK} Vault mirror:     {config.FRAGMENTS_DIR}")
    click.echo("Next steps:")
    click.echo("  hippo register         # wire MCP server into all AI clients")
    click.echo("  hippo inject           # generate the auto-inject block")
    click.echo("  bash scripts/install.sh  # install launchd agent for decay + inject")


@cli.command()
def doctor() -> None:
    """Health check across DB, vault, clients, and daemon."""
    _bootstrap()

    from hippocampus.clients import mcp_config as mcp_cfg
    from hippocampus.clients.registry import CLIENTS
    from hippocampus.storage import fragments as F

    ok = []
    warn = []

    # DB reachable + has expected schema
    try:
        n = F.count()
        ok.append(f"SQLite OK ({n} fragments) at {config.DB_PATH}")
    except Exception as e:  # noqa: BLE001
        warn.append(f"SQLite FAIL: {e}")

    # Vault mirror dir exists
    if config.FRAGMENTS_DIR.exists():
        mirror_count = len(list(config.FRAGMENTS_DIR.glob("*.md")))
        ok.append(f"Vault mirror OK ({mirror_count} files) at {config.FRAGMENTS_DIR}")
    else:
        warn.append(f"Vault mirror MISSING: {config.FRAGMENTS_DIR}")

    # Injection file present?
    if config.INJECTION_FILE.exists():
        ok.append(f"Injection file OK at {config.INJECTION_FILE}")
    else:
        warn.append(f"Injection file MISSING (run `hippo inject`)")

    # Each client
    from hippocampus.clients.injector import config as _c  # noqa: F401

    for spec in CLIENTS:
        rules_text = spec.rules_path.read_text(encoding="utf-8") if spec.rules_path.exists() else ""
        long_ok = config.INJECTION_MARKER_START in rules_text
        working_ok = config.WORKING_MARKER_START in rules_text
        mcp_ok = mcp_cfg.is_registered(spec)
        badge_long = OK_MARK if long_ok else MISS_MARK
        badge_work = OK_MARK if working_ok else MISS_MARK
        badge_mcp = OK_MARK if mcp_ok else MISS_MARK
        target = "ok" if long_ok and working_ok and mcp_ok else "warn"
        line = f"{spec.label:<14} long:{badge_long} working:{badge_work} mcp:{badge_mcp}  ({spec.rules_path})"
        (ok if target == "ok" else warn).append(line)

    # Launchd agent (macOS only)
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.hippocampus.daemon.plist"
    if plist_path.exists():
        ok.append(f"launchd plist OK at {plist_path}")
    else:
        warn.append("launchd plist MISSING (run scripts/install.sh to install)")

    # Settings snapshot
    settings = config.all_settings()
    ok.append(
        f"settings: working_block_mode={settings['working_block_mode']}, "
        f"auto_end_idle_minutes={settings['auto_end_idle_minutes']}, "
        f"semantic_weight={settings['semantic_weight']}, "
        f"wiki_enabled={settings['wiki_enabled']}"
    )

    try:
        from hippocampus.wiki import storage as wiki_storage
        projects = wiki_storage.list_projects(limit=1000)
        if projects:
            ok.append(f"wiki: {len(projects)} initialized project(s)")
        elif settings.get("wiki_enabled"):
            warn.append("wiki: enabled but no initialized projects (run `hippo wiki init`)")
    except Exception as e:  # noqa: BLE001
        warn.append(f"wiki: status failed ({e})")

    # Embeddings status
    try:
        from hippocampus.embeddings import search as semantic_search
        st = semantic_search.stats()
        if st["provider_available"]:
            ok.append(
                f"embeddings: {st['embedded']}/{st['total_fragments']} covered "
                f"(model={st['model']}, dim={st['dim']})"
            )
        else:
            warn.append(
                "embeddings: provider unavailable — install with "
                "`uv pip install -e '.[semantic]'` to enable semantic recall"
            )
    except Exception as e:  # noqa: BLE001
        warn.append(f"embeddings: stats failed ({e})")

    # Auto-trigger hooks status
    try:
        from hippocampus.clients.hooks import status as hooks_status
        for hs in hooks_status():
            installed = hs["installed"]
            parts = []
            all_present = True
            for ev in installed:
                ok_glyph = OK_MARK if installed[ev] else MISS_MARK
                if not installed[ev]:
                    all_present = False
                parts.append(f"{ev}:{ok_glyph}")
            line = f"hooks/{hs['client']:<11} " + " ".join(parts)
            if all_present:
                ok.append(line)
            else:
                warn.append(line + "  (run `hippo install-hooks`)")
    except Exception as e:  # noqa: BLE001
        warn.append(f"hooks: status failed ({e})")

    for line in ok:
        click.echo(click.style("OK  ", fg="green") + line)
    for line in warn:
        click.echo(click.style("WARN", fg="yellow") + " " + line)

    if warn:
        sys.exit(1)


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------


@cli.group()
def session() -> None:
    """Per-client session management."""


@session.command("start")
@click.option("--client", "-c", required=True, help="Client name (devin, claude-code, ...)")
@click.option("--session-key", default=None, help="Override derived context key")
@click.option("--new", "force_new", is_flag=True, help="Force a fresh session instead of reusing the active context session")
def session_start(client: str, session_key: Optional[str], force_new: bool) -> None:
    _bootstrap()
    from hippocampus.storage import sessions

    sid = (
        sessions.open_session(client, session_key=session_key)
        if force_new
        else sessions.ensure_session(client, session_key=session_key)
    )
    click.echo(sid)


@session.command("end")
@click.option("--client", "-c", required=True)
@click.option("--session-key", default=None, help="Override derived context key")
def session_end(client: str, session_key: Optional[str]) -> None:
    _bootstrap()
    from hippocampus.storage import sessions

    sid = sessions.current_session_id(client, session_key=session_key, open_if_missing=False)
    sessions.close_session(sid)
    click.echo(f"closed {sid}")


@session.command("status")
@click.option("--client", "-c", required=False)
@click.option("--session-key", default=None, help="Override derived context key when --client is set")
def session_status(client: Optional[str], session_key: Optional[str]) -> None:
    _bootstrap()
    from hippocampus.clients.registry import CLIENTS
    from hippocampus.storage import sessions

    names = [client] if client else [c.name for c in CLIENTS]
    for n in names:
        if n is None:
            continue
        if client:
            key = sessions.derive_session_key(session_key)
            try:
                sid = sessions.current_session_id(n, session_key=key, open_if_missing=False)
                click.echo(f"{n}[{key}]: {sid}")
            except RuntimeError:
                click.echo(f"{n}[{key}]: <no session>")
            continue

        client_dir = config.SESSION_POINTER_DIR / n
        legacy = config.SESSION_POINTER_DIR / f"{n}.id"
        printed = False
        if client_dir.exists():
            for ptr in sorted(client_dir.glob("*.id")):
                key = ptr.name[:-3] if ptr.name.endswith(".id") else ptr.stem
                click.echo(f"{n}[{key}]: {ptr.read_text().strip()}")
                printed = True
        if legacy.exists():
            click.echo(f"{n}[legacy]: {legacy.read_text().strip()}")
            printed = True
        if not printed:
            click.echo(f"{n}: <no session>")


# ---------------------------------------------------------------------------
# remember / recall / forget / pin / unpin
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--content", "-c", help="Fragment content (or pass via stdin)")
@click.option("--summary", "-s", default=None)
@click.option("--tags", "-t", multiple=True, help="Tag (repeatable)")
@click.option("--source-type", default="manual")
@click.option("--source-ref", default=None)
@click.option("--pinned/--no-pinned", default=False)
def remember(content: Optional[str], summary: Optional[str], tags: tuple, source_type: str, source_ref: Optional[str], pinned: bool) -> None:
    """Store a synthesized fragment."""
    _bootstrap()
    if content is None:
        content = sys.stdin.read()
    from hippocampus.mcp import tools

    out = tools.remember(
        content=content, summary=summary, tags=list(tags),
        source_type=source_type, source_ref=source_ref, pinned=pinned,
    )
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@cli.command()
@click.argument("query")
@click.option("--limit", "-n", default=5, show_default=True)
@click.option("--min-confidence", default=0.0, show_default=True)
@click.option("--context-tag", default=None)
@click.option("--client", "-c", default=None, help="Override HIPPOCAMPUS_CLIENT for this call")
def recall(query: str, limit: int, min_confidence: float, context_tag: Optional[str], client: Optional[str]) -> None:
    """Search + boost."""
    if client:
        os.environ["HIPPOCAMPUS_CLIENT"] = client
    _bootstrap()
    from hippocampus.mcp import tools
    out = tools.recall(query=query, limit=limit, min_confidence=min_confidence, context_tag=context_tag)
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@cli.command()
@click.argument("fragment_id")
@click.option("--reason", default=None)
def forget(fragment_id: str, reason: Optional[str]) -> None:
    _bootstrap()
    from hippocampus.mcp import tools
    click.echo(json.dumps(tools.forget(fragment_id, reason), indent=2, ensure_ascii=False))


@cli.command()
@click.argument("fragment_id")
def pin(fragment_id: str) -> None:
    _bootstrap()
    from hippocampus.mcp import tools
    click.echo(json.dumps(tools.pin(fragment_id), indent=2, ensure_ascii=False))


@cli.command()
@click.argument("fragment_id")
def unpin(fragment_id: str) -> None:
    _bootstrap()
    from hippocampus.mcp import tools
    click.echo(json.dumps(tools.unpin(fragment_id), indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# stats / list / top
# ---------------------------------------------------------------------------


@cli.command()
def stats() -> None:
    _bootstrap()
    from hippocampus.mcp import tools
    click.echo(json.dumps(tools.get_stats(), indent=2, ensure_ascii=False))


@cli.command("health")
@click.option("--duplicates", is_flag=True, help="Include embedding duplicate candidates")
def health_cmd(duplicates: bool) -> None:
    """Operational health snapshot: sessions, mirror, tags, embeddings, duplicates."""
    _bootstrap()
    from hippocampus import maintenance

    click.echo(json.dumps(maintenance.health_snapshot(include_duplicates=duplicates), indent=2, ensure_ascii=False))


@cli.command("cleanup-sessions")
@click.option("--dry-run/--commit", default=True, help="Preview without mutating")
def cleanup_sessions_cmd(dry_run: bool) -> None:
    """Clean no-activity sessions and close duplicate active contexts."""
    _bootstrap()
    from hippocampus import maintenance

    click.echo(json.dumps(maintenance.cleanup_sessions(dry_run=dry_run), indent=2, ensure_ascii=False))


@cli.command("reconcile-mirror")
@click.option("--dry-run/--commit", default=True, help="Preview without mutating")
def reconcile_mirror_cmd(dry_run: bool) -> None:
    """Reconcile the markdown mirror with SQLite canonical fragments."""
    _bootstrap()
    from hippocampus import maintenance

    click.echo(json.dumps(maintenance.reconcile_mirror(dry_run=dry_run), indent=2, ensure_ascii=False))


@cli.command("list")
@click.option("--tag", default=None)
@click.option("--min-confidence", default=0.0, show_default=True)
@click.option("--limit", default=20, show_default=True)
def list_cmd(tag: Optional[str], min_confidence: float, limit: int) -> None:
    _bootstrap()
    from hippocampus.mcp import tools
    click.echo(json.dumps(tools.list_fragments(tag=tag, min_confidence=min_confidence, limit=limit), indent=2, ensure_ascii=False))


@cli.command()
@click.option("--limit", default=None, type=int, help=f"default: {config.TOP_N_DEFAULT}")
def top(limit: Optional[int]) -> None:
    _bootstrap()
    from hippocampus.mcp import tools
    click.echo(json.dumps(tools.top_fragments(limit=limit), indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# decay / archive / inject / register
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--dry-run/--commit", default=False, help="Preview without mutating")
def decay(dry_run: bool) -> None:
    _bootstrap()
    from hippocampus.dynamics import decay as decay_dyn
    result = decay_dyn.run_decay_cycle(dry_run=dry_run)
    click.echo(json.dumps(result.as_dict(), indent=2))


@cli.command()
@click.option("--dry-run/--commit", default=False)
def archive(dry_run: bool) -> None:
    _bootstrap()
    from hippocampus.dynamics import archive as archive_dyn
    result = archive_dyn.run_archive_cycle(dry_run=dry_run)
    click.echo(json.dumps(result.as_dict(), indent=2))


@cli.command()
@click.option("--limit", default=None, type=int, help=f"top-N (default: {config.TOP_N_DEFAULT})")
@click.option("--only", multiple=True, help="Only inject into these clients (repeatable)")
@click.option("--dry-run/--commit", default=False)
def inject(limit: Optional[int], only: tuple, dry_run: bool) -> None:
    """Regenerate the top-N block AND the working-memory block in every client."""
    _bootstrap()
    from hippocampus.clients.injector import (
        format_injection_block,
        format_working_block,
        upsert_block,
        upsert_working_block,
    )
    from hippocampus.clients.registry import CLIENTS
    from hippocampus.dynamics import ranking
    from hippocampus.mcp.tools import _session_row
    from hippocampus.storage import ledger as ledger_store, sessions

    frags = ranking.top_n(limit=limit)
    long_block = format_injection_block(frags)

    if not dry_run:
        config.INJECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.INJECTION_FILE.write_text(long_block, encoding="utf-8")
    click.echo(f"canonical: {config.INJECTION_FILE} ({len(frags)} fragments)")

    targets = [c for c in CLIENTS if not only or c.name in only]
    for spec in targets:
        # Render this client's working block (may be empty if no session yet)
        try:
            sid = sessions.current_session_id(spec.name, open_if_missing=False)
            entries = ledger_store.current_entries(sid)
            started_at = (_session_row(sid) or {}).get("started_at")
        except RuntimeError:
            sid, entries, started_at = None, [], None
        handoff_file = None
        if sid is not None:
            from hippocampus import handoff as handoff_mod

            candidate = handoff_mod.handoff_path(sid)
            if candidate.exists():
                handoff_file = str(candidate)
        working_block = format_working_block(
            session_id=sid,
            client=spec.name,
            started_at=started_at,
            entries=entries,
            handoff_path=handoff_file,
        )

        if dry_run:
            click.echo(f"{spec.name}: would upsert 2 blocks into {spec.rules_path}")
            continue
        try:
            _, l_reason = upsert_block(
                spec.rules_path, long_block,
                create_if_missing=True,
                header_when_creating=spec.creation_header,
            )
            _, w_reason = upsert_working_block(
                spec.rules_path, working_block,
                create_if_missing=True,
                header_when_creating=spec.creation_header,
            )
            click.echo(f"{spec.name}: long={l_reason} working={w_reason} ({spec.rules_path})")
        except Exception as e:  # noqa: BLE001
            click.echo(f"{spec.name}: error {e}")


@cli.command()
def register() -> None:
    """Register Hippocampus MCP server in every client's config."""
    _bootstrap()
    from hippocampus.clients.mcp_config import register_all
    for name, changed, msg in register_all():
        flag = "+" if changed else "="
        click.echo(f"[{flag}] {msg}")


@cli.command()
def unregister() -> None:
    """Remove Hippocampus MCP server from every client's config."""
    _bootstrap()
    from hippocampus.clients.mcp_config import unregister
    from hippocampus.clients.registry import CLIENTS
    for spec in CLIENTS:
        changed, msg = unregister(spec)
        flag = "-" if changed else "="
        click.echo(f"[{flag}] {msg}")


@cli.command("strip-blocks")
def strip_blocks() -> None:
    """Remove BOTH Hippocampus marker blocks from every client's rules file."""
    _bootstrap()
    from hippocampus.clients.injector import remove_block, remove_working_block
    from hippocampus.clients.registry import CLIENTS
    for spec in CLIENTS:
        if not spec.rules_path.exists():
            click.echo(f"[.] {spec.name}: no rules file")
            continue
        a = remove_block(spec.rules_path)
        b = remove_working_block(spec.rules_path)
        flag = "-" if (a or b) else "="
        click.echo(f"[{flag}] {spec.name}: long={a} working={b} ({spec.rules_path})")


# ---------------------------------------------------------------------------
# progress (working memory)
# ---------------------------------------------------------------------------


@cli.group()
def progress() -> None:
    """Working-memory ledger — the per-session asks/dones/decisions."""


@progress.command("log")
@click.argument("kind", type=click.Choice(["goal", "ask", "done", "blocker", "decision", "next", "note"]))
@click.argument("content")
@click.option("--details", default=None)
@click.option("--client", "-c", default=None, help="Override HIPPOCAMPUS_CLIENT")
def progress_log(kind: str, content: str, details: Optional[str], client: Optional[str]) -> None:
    """Append one working-memory entry. Refreshes the WORKING block for that client."""
    if client:
        os.environ["HIPPOCAMPUS_CLIENT"] = client
    _bootstrap()
    from hippocampus.mcp import tools
    out = tools.log_progress(kind=kind, content=content, details=details)
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@progress.command("show")
@click.option("--client", "-c", default=None)
@click.option("--full/--recent", default=False)
def progress_show(client: Optional[str], full: bool) -> None:
    if client:
        os.environ["HIPPOCAMPUS_CLIENT"] = client
    _bootstrap()
    from hippocampus.mcp import tools
    out = tools.get_progress(client=client, full=full)
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@progress.command("end")
@click.option("--distill/--no-distill", default=False, help="Store a long-term fragment summarising the session")
@click.option("--summary", default=None)
@click.option("--tag", "tags", multiple=True)
@click.option("--client", "-c", default=None)
def progress_end(distill: bool, summary: Optional[str], tags: tuple, client: Optional[str]) -> None:
    if client:
        os.environ["HIPPOCAMPUS_CLIENT"] = client
    _bootstrap()
    from hippocampus.mcp import tools
    out = tools.end_progress(distill_to_fragment=distill, summary=summary, tags=list(tags), client=client)
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@progress.command("clear")
@click.option("--confirm", is_flag=True, help="Actually drop the entries (without this flag it's a preview)")
@click.option("--client", "-c", default=None)
def progress_clear(confirm: bool, client: Optional[str]) -> None:
    if client:
        os.environ["HIPPOCAMPUS_CLIENT"] = client
    _bootstrap()
    from hippocampus.storage import ledger as ledger_store, sessions
    client_name = (client or os.environ.get("HIPPOCAMPUS_CLIENT", "cli")).lower()
    try:
        sid = sessions.current_session_id(client_name, open_if_missing=False)
    except RuntimeError:
        click.echo(f"{client_name}: no active session")
        return
    if not confirm:
        entries = ledger_store.current_entries(sid)
        click.echo(f"{client_name}: session {sid} has {len(entries)} entries (pass --confirm to delete)")
        return
    n = ledger_store.delete_session_ledger(sid)
    click.echo(f"{client_name}: deleted {n} entries from session {sid}")


@progress.command("undo")
@click.option("--client", "-c", default=None)
def progress_undo(client: Optional[str]) -> None:
    """Remove the most recent ledger entry for the calling client."""
    if client:
        os.environ["HIPPOCAMPUS_CLIENT"] = client
    _bootstrap()
    from hippocampus.mcp import tools
    out = tools.undo_last_entry(client=client)
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@cli.command("handoff")
@click.option("--client", "-c", default=None, help="Override HIPPOCAMPUS_CLIENT")
@click.option("--path-only", is_flag=True, help="Print only the handoff file path")
def handoff_cmd(client: Optional[str], path_only: bool) -> None:
    """Show the current (or most recent) session's handoff document."""
    if client:
        os.environ["HIPPOCAMPUS_CLIENT"] = client
    _bootstrap()
    from hippocampus.mcp import tools
    out = tools.get_handoff(client=client)
    if path_only:
        click.echo(out.get("path") or "")
        return
    if out.get("content"):
        click.echo(out["content"])
    else:
        click.echo(json.dumps(out, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# transcript
# ---------------------------------------------------------------------------


@cli.group()
def transcript() -> None:
    """Raw/visible session transcript history."""


@transcript.command("log")
@click.argument("role", type=click.Choice(["user", "assistant", "assistant_summary", "reasoning_summary", "system", "tool"]))
@click.argument("content", required=False)
@click.option("--stdin", "read_stdin", is_flag=True, help="Read content from stdin")
@click.option("--source-event", default=None)
@click.option("--client", "-c", default=None)
def transcript_log(role: str, content: Optional[str], read_stdin: bool, source_event: Optional[str], client: Optional[str]) -> None:
    """Append one transcript row for the current session context."""
    if client:
        os.environ["HIPPOCAMPUS_CLIENT"] = client
    _bootstrap()
    if read_stdin or content is None:
        content = sys.stdin.read()
    from hippocampus.mcp import tools

    out = tools.log_transcript(
        role=role,
        content=content or "",
        source_event=source_event,
        client=client,
    )
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@transcript.command("show")
@click.option("--client", "-c", default=None)
@click.option("--limit", default=200, show_default=True)
def transcript_show(client: Optional[str], limit: int) -> None:
    if client:
        os.environ["HIPPOCAMPUS_CLIENT"] = client
    _bootstrap()
    from hippocampus.mcp import tools

    out = tools.get_transcript(client=client, limit=limit)
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@cli.group(name="config")
def config_group() -> None:
    """View or change user-mutable settings (working-block mode, idle timer, ...)."""


@config_group.command("show")
def config_show() -> None:
    _bootstrap()
    click.echo(json.dumps(
        {
            "path": str(config.config_path()),
            "settings": config.all_settings(),
        },
        indent=2,
    ))


@config_group.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Persist a setting. `value` is parsed as JSON when possible, else kept as string."""
    _bootstrap()
    import json as _json
    try:
        parsed = _json.loads(value)
    except _json.JSONDecodeError:
        parsed = value
    normalised_key = key.replace("-", "_").lower()
    config.set_setting(normalised_key, parsed)
    click.echo(json.dumps({"set": {normalised_key: parsed}, "all": config.all_settings()}, indent=2))


# ---------------------------------------------------------------------------
# embeddings / reindex
# ---------------------------------------------------------------------------


@cli.command("reindex")
@click.option("--force/--missing-only", default=False, help="Re-embed everything (default: only missing)")
@click.option("--batch", default=64, show_default=True)
def reindex_cmd(force: bool, batch: int) -> None:
    """Embed fragments for semantic search."""
    _bootstrap()
    from hippocampus.embeddings import search as semantic_search

    result = semantic_search.reindex(force=force, batch=batch)
    click.echo(json.dumps(result, indent=2))


@cli.group(name="embeddings")
def embeddings_group() -> None:
    """Embedding status + maintenance."""


@embeddings_group.command("stats")
def embeddings_stats() -> None:
    _bootstrap()
    from hippocampus.embeddings import search as semantic_search

    click.echo(json.dumps(semantic_search.stats(), indent=2))


@embeddings_group.command("bench")
@click.option(
    "--models", "-m",
    default="BAAI/bge-small-en-v1.5,dunzhang/stella_en_1.5B_v5",
    help="Comma-separated list of model names to benchmark.",
)
@click.option(
    "--provider", "-p",
    default="sentence-transformers",
    type=click.Choice(["fastembed", "sentence-transformers"]),
    show_default=True,
)
@click.option(
    "--queries", "-q",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="JSONL file of {query, expected_id} rows. If omitted, uses a self-retrieval test built from every fragment's summary.",
)
def embeddings_bench(models: str, provider: str, queries: Optional[Path]) -> None:
    """Benchmark multiple embedding models on your real fragments.

    Runs hit@1, hit@5, latency for each model. Does NOT touch your
    persistent embeddings — scratch-indexes in memory.
    """
    _bootstrap()
    from hippocampus.embeddings import bench as bench_mod

    model_list = [m.strip() for m in models.split(",") if m.strip()]
    result = bench_mod.bench(
        models=model_list,
        provider=provider,
        queries_path=queries,
    )
    click.echo(json.dumps(result, indent=2))


@cli.command("context")
@click.option("--client", "-c", required=True, help="Client name (devin, claude-code, ...)")
@click.option("--query", "-q", default=None, help="Recall query (e.g. the user prompt or compaction summary)")
@click.option("--no-working", is_flag=True, help="Skip the live working ledger")
@click.option("--no-fragments", is_flag=True, help="Skip the top long-term fragments")
@click.option("--fragment-limit", type=int, default=None)
@click.option("--budget", type=int, default=None, help="Char budget for the rendered payload")
@click.option("--event", default=None, help="Hook event name for provenance (SessionStart, ...)")
def context_cmd(
    client: str,
    query: Optional[str],
    no_working: bool,
    no_fragments: bool,
    fragment_limit: Optional[int],
    budget: Optional[int],
    event: Optional[str],
) -> None:
    """Render a plain-markdown context payload for lifecycle hooks.

    Emits the live working ledger + top long-term fragments, intended for
    `hookSpecificOutput.additionalContext` in SessionStart / UserPromptSubmit
    / PostCompaction hooks. Output is text, not JSON — the hook script wraps
    it in the right JSON envelope per event.
    """
    os.environ["HIPPOCAMPUS_CLIENT"] = client
    _bootstrap()
    from hippocampus.clients import hook_context

    settings = config.all_settings()
    payload = hook_context.render_context(
        client=client,
        query=query,
        include_working=(not no_working) and bool(settings.get("hook_inject_working", True)),
        include_fragments=(not no_fragments) and bool(settings.get("hook_inject_fragments", True)),
        fragment_limit=int(fragment_limit if fragment_limit is not None else settings.get("hook_fragment_limit", 5)),
        char_budget=int(budget if budget is not None else settings.get("hook_inject_budget_chars", 3500)),
        event_name=event,
    )
    sys.stdout.write(payload)


@cli.command("autoremember")
@click.argument("prompt", required=False)
@click.option("--client", "-c", default="cli", help="Client name (devin, claude-code, ...)")
@click.option("--stdin", is_flag=True, help="Read the prompt from stdin instead of argv")
@click.option("--quiet", is_flag=True, help="Only emit the fragment id on success, nothing on no-op")
def autoremember_cmd(prompt: Optional[str], client: str, stdin: bool, quiet: bool) -> None:
    """Scan a user prompt for autoremember triggers and persist if found.

    Triggers: remember, always, never, don't forget, from now on, next time,
    add to (global) rules, keep in mind, make sure you. Designed to be wired
    into the UserPromptSubmit hook so the AI's "user told me to always X"
    moments become durable fragments automatically.
    """
    if stdin or prompt is None:
        prompt = sys.stdin.read()
    os.environ["HIPPOCAMPUS_CLIENT"] = client
    _bootstrap()
    from hippocampus.mcp import tools
    out = tools.auto_remember(prompt or "", client=client)
    if quiet:
        if out.get("remembered"):
            click.echo(out["fragment"]["id"])
        return
    click.echo(json.dumps(out, indent=2))


@cli.command("dedup")
@click.option("--threshold", type=float, default=None, help="Cosine similarity above which pairs are duplicates (default 0.95)")
@click.option("--limit", type=int, default=20, help="Max pairs to show")
@click.option("--merge", nargs=2, type=str, default=None, help="Merge fragment A into fragment B (keeper, loser)")
def dedup_cmd(threshold: Optional[float], limit: int, merge: Optional[tuple[str, str]]) -> None:
    """Find near-duplicate fragments by cosine similarity, or merge a specific pair.

    Without --merge, prints candidates as JSON. Use this output to decide which
    pairs to merge with `hippo dedup --merge <keeper> <loser>`.
    """
    _bootstrap()
    from hippocampus.embeddings import dedup as dedup_mod

    if merge:
        keeper, loser = merge
        out = dedup_mod.merge(keeper, loser)
        click.echo(json.dumps(out, indent=2))
        return

    pairs = dedup_mod.find_duplicates(threshold=threshold, limit=limit)
    out = [
        {"keeper": p.keeper, "loser": p.loser, "score": round(p.score, 4)}
        for p in pairs
    ]
    click.echo(json.dumps({"threshold": threshold, "count": len(out), "pairs": out}, indent=2))


@cli.command("observe")
@click.option("--source", type=str, default=None, help="JSONL file path (default: ~/.hippocampus/observations.jsonl)")
@click.option("--dry-run", is_flag=True, help="Show what would be created without writing")
def observe_cmd(source: Optional[str], dry_run: bool) -> None:
    """Ingest external observations from a JSONL file into auto-observed fragments.

    Each line must be a JSON object with `content` (required), `summary` (optional),
    `tags` (optional list), `source_ref` (optional). Tracks offset between runs in
    `~/.hippocampus/.observe.offset` so re-running only processes new lines.

    Designed for shell-side / git-side / cron-side automation: append observations
    to the JSONL file from anywhere; this CLI turns them into fragments idempotently.
    """
    _bootstrap()
    from pathlib import Path as _P
    from hippocampus.mcp import tools as T

    src = _P(source) if source else (config.HIPPOCAMPUS_HOME / "observations.jsonl")
    offset_path = config.HIPPOCAMPUS_HOME / ".observe.offset"
    if not src.exists():
        click.echo(json.dumps({"status": "no_source", "path": str(src)}, indent=2))
        return

    last_offset = 0
    if offset_path.exists():
        try:
            last_offset = int(offset_path.read_text(encoding="utf-8").strip() or "0")
        except (ValueError, OSError):
            last_offset = 0

    created: list[dict] = []
    with src.open("r", encoding="utf-8") as fh:
        fh.seek(last_offset)
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (record.get("content") or "").strip()
            if not content:
                continue
            summary = (record.get("summary") or "").strip() or content[:120]
            tags = list(record.get("tags") or []) + ["auto-observed"]
            source_ref = record.get("source_ref")

            if dry_run:
                created.append({"summary": summary, "tags": tags, "source_ref": source_ref})
                continue

            res = T.remember(
                content=content,
                summary=summary,
                tags=tags,
                source_type="auto-observed",
                source_ref=source_ref,
                pinned=False,
            )
            frag = res.get("fragment") or {}
            # Hard-set the lower initial confidence for auto-observed entries.
            obs_conf = float(config.get_setting("observe_default_confidence") or 0.30)
            try:
                from hippocampus.storage import fragments as frag_store
                frag_store.update_fields(frag.get("id"), confidence=obs_conf)
            except Exception:
                pass
            created.append({"id": frag.get("id"), "summary": frag.get("summary")})

        new_offset = fh.tell()

    if not dry_run:
        offset_path.write_text(str(new_offset), encoding="utf-8")

    click.echo(json.dumps({
        "source": str(src),
        "previous_offset": last_offset,
        "new_offset": new_offset,
        "dry_run": dry_run,
        "created": created,
        "count": len(created),
    }, indent=2))


# ---------------------------------------------------------------------------
# wiki (DB-backed LLM Wiki)
# ---------------------------------------------------------------------------


@cli.group("wiki")
def wiki_group() -> None:
    """Database-backed LLM Wiki workflows."""


@wiki_group.command("init")
@click.option("--project", "-p", default=None, help="Project key; defaults to current workspace")
@click.option("--title", default=None, help="Human-readable wiki title")
@click.option("--workspace-path", default=None, help="Workspace path to associate with the project")
@click.option("--export-root", default=None, help="Where markdown files should be materialized")
@click.option("--materialize", is_flag=True, help="Also create/export markdown files")
def wiki_init_cmd(project: Optional[str], title: Optional[str], workspace_path: Optional[str], export_root: Optional[str], materialize: bool) -> None:
    _bootstrap()
    from hippocampus.mcp import tools

    out = tools.wiki_init(
        project=project,
        title=title,
        workspace_path=workspace_path,
        export_root=export_root,
        materialize=materialize,
    )
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@wiki_group.command("status")
@click.option("--project", "-p", default=None)
def wiki_status_cmd(project: Optional[str]) -> None:
    _bootstrap()
    from hippocampus.mcp import tools

    click.echo(json.dumps(tools.wiki_status(project=project), indent=2, ensure_ascii=False))


@wiki_group.command("lint")
@click.option("--project", "-p", default=None)
def wiki_lint_cmd(project: Optional[str]) -> None:
    _bootstrap()
    from hippocampus.mcp import tools

    click.echo(json.dumps(tools.wiki_lint(project=project), indent=2, ensure_ascii=False))


@wiki_group.command("export")
@click.option("--project", "-p", default=None)
def wiki_export_cmd(project: Optional[str]) -> None:
    _bootstrap()
    from hippocampus.mcp import tools

    click.echo(json.dumps(tools.wiki_export(project=project), indent=2, ensure_ascii=False))


@wiki_group.command("ingest")
@click.argument("raw_path")
@click.option("--project", "-p", default=None)
@click.option("--dry-run", is_flag=True)
@click.option("--materialize", is_flag=True)
def wiki_ingest_cmd(raw_path: str, project: Optional[str], dry_run: bool, materialize: bool) -> None:
    _bootstrap()
    from hippocampus.mcp import tools

    out = tools.wiki_ingest(raw_path=raw_path, project=project, dry_run=dry_run, materialize=materialize)
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@wiki_group.command("query")
@click.argument("question")
@click.option("--project", "-p", default=None)
@click.option("--limit", default=None, type=int)
def wiki_query_cmd(question: str, project: Optional[str], limit: Optional[int]) -> None:
    _bootstrap()
    from hippocampus.mcp import tools

    click.echo(json.dumps(tools.wiki_query(question=question, project=project, limit=limit), indent=2, ensure_ascii=False))


@wiki_group.command("file-answer")
@click.argument("title")
@click.option("--project", "-p", default=None)
@click.option("--stdin", "read_stdin", is_flag=True, help="Read answer markdown from stdin")
@click.option("--content", "-c", default=None, help="Answer markdown")
@click.option("--materialize", is_flag=True)
def wiki_file_answer_cmd(title: str, project: Optional[str], read_stdin: bool, content: Optional[str], materialize: bool) -> None:
    _bootstrap()
    if read_stdin or content is None:
        content = sys.stdin.read()
    from hippocampus.mcp import tools

    out = tools.wiki_file_answer(title=title, markdown=content or "", project=project, materialize=materialize)
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@wiki_group.command("index")
@click.option("--project", "-p", default=None)
@click.option("--render", "render_only", is_flag=True, help="Print rendered markdown only")
def wiki_index_cmd(project: Optional[str], render_only: bool) -> None:
    _bootstrap()
    from hippocampus.mcp import tools

    out = tools.wiki_index(project=project)
    if render_only and out.get("ok"):
        click.echo(out.get("markdown", ""))
        return
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@wiki_group.command("log")
@click.option("--project", "-p", default=None)
@click.option("--tail", default=20, show_default=True)
@click.option("--render", "render_only", is_flag=True, help="Print rendered markdown only")
def wiki_log_cmd(project: Optional[str], tail: int, render_only: bool) -> None:
    _bootstrap()
    from hippocampus.mcp import tools

    out = tools.wiki_log(project=project, limit=tail)
    if render_only and out.get("ok"):
        click.echo(out.get("markdown", ""))
        return
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@cli.command("install-hooks")
def install_hooks_cmd() -> None:
    """Install SessionStart + UserPromptSubmit hooks into Devin + Claude Code.

    These hooks auto-open a Hippocampus session on start-up and log every
    user message as kind='ask' — so the AI never has to remember to call
    log_progress for asks. Run once; installs are idempotent.
    """
    _bootstrap()
    from hippocampus.clients.hooks import install_all
    results = install_all()
    click.echo(json.dumps(results, indent=2))


@cli.command("uninstall-hooks")
def uninstall_hooks_cmd() -> None:
    """Remove Hippocampus hook entries from every client's config.

    Leaves any other hooks in those configs alone.
    """
    _bootstrap()
    from hippocampus.clients.hooks import uninstall_all
    results = uninstall_all()
    click.echo(json.dumps(results, indent=2))


@cli.command("hooks-status")
def hooks_status_cmd() -> None:
    """Report which clients have the auto-trigger hooks installed."""
    _bootstrap()
    from hippocampus.clients.hooks import status
    click.echo(json.dumps(status(), indent=2))


# ---------------------------------------------------------------------------
# web UI
# ---------------------------------------------------------------------------


@cli.command("web")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=7878, show_default=True)
@click.option("--no-browser", is_flag=True, help="Don't auto-open the browser")
def web_cmd(host: str, port: int, no_browser: bool) -> None:
    """Start the Hippocampus browser UI."""
    _bootstrap()
    try:
        from hippocampus.web.server import run
    except RuntimeError as e:
        raise click.ClickException(str(e))
    run(host=host, port=port, open_browser=not no_browser)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    cli()
