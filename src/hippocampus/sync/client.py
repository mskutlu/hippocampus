"""Device sync client (V11): push local changes, pull other devices' ops.

Never imported by MCP tools, hooks, or the web UI. Runs from `hippo sync`
(also chained after `hippo inject` when `sync_enabled`), so the MCP request
path is untouched. Transport is stdlib urllib; `transport` is injectable for
tests.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ulid import ULID

from hippocampus import config, projects
from hippocampus.storage import fragments as frag_store
from hippocampus.storage.db import get_conn, get_ro_conn
from hippocampus.sync import merge

BATCH = 200
Transport = Callable[[str, str, dict | None], dict]  # (method, path, body) -> json


class SyncError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --- state -----------------------------------------------------------------

def get_state(key: str) -> str | None:
    with get_ro_conn() as conn:
        row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(key: str, value: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sync_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def device_id() -> str:
    path = config.HIPPOCAMPUS_HOME / "device_id"
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    config.ensure_dirs()
    new = f"dev_{ULID()}"
    path.write_text(new + "\n", encoding="utf-8")
    config.secure_file(path)
    return new


# --- transport ---------------------------------------------------------------

def http_transport(base_url: str, token: str, timeout: float = 20.0) -> Transport:
    base = base_url.rstrip("/")

    def call(method: str, path: str, body: dict | None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raise SyncError(f"{method} {path} -> HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SyncError(f"{method} {path} -> {exc}") from exc

    return call


def configured_transport() -> Transport:
    url = (config.get_setting("sync_url") or "").strip()
    token = (config.get_setting("sync_token") or "").strip()
    if not url or not token:
        raise SyncError("sync_url and sync_token must be set (hippo config set ...)")
    return http_transport(url, token)


# --- collect local ops -------------------------------------------------------

def _fragment_payload(conn, row) -> dict[str, Any]:
    payload = {k: row[k] for k in row.keys()}
    payload["tags"] = [
        r["tag"] for r in conn.execute("SELECT tag FROM fragment_tags WHERE fragment_id = ?", (row["id"],)).fetchall()
    ]
    emb = conn.execute(
        "SELECT vector, dim, model FROM fragment_embeddings WHERE fragment_id = ?", (row["id"],)
    ).fetchone()
    if emb:
        payload["embedding"] = {
            "model": emb["model"],
            "dim": int(emb["dim"]),
            "vector_b64": base64.b64encode(emb["vector"]).decode("ascii"),
        }
    return payload


def collect_ops(since: str) -> list[dict[str, Any]]:
    """Local changes strictly newer than `since` (microsecond timestamps)."""
    ops: list[dict[str, Any]] = []
    with get_ro_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM fragments WHERE updated_at > ? OR COALESCE(last_accessed_at, '') > ?",
            (since, since),
        ).fetchall()
        for row in rows:
            payload = _fragment_payload(conn, row)
            ops.append({
                "entity": "fragment", "entity_id": row["id"], "op": "upsert",
                "updated_at": max(row["updated_at"] or "", row["last_accessed_at"] or ""),
                "payload": payload,
            })
        for row in conn.execute("SELECT fragment_id, deleted_at FROM fragment_tombstones WHERE deleted_at > ?", (since,)).fetchall():
            ops.append({
                "entity": "tombstone", "entity_id": row["fragment_id"], "op": "delete",
                "updated_at": row["deleted_at"], "payload": {"deleted_at": row["deleted_at"]},
            })
        for row in conn.execute("SELECT * FROM associations WHERE last_co_accessed_at > ?", (since,)).fetchall():
            ops.append({
                "entity": "association", "entity_id": f"{row['fragment_a']}|{row['fragment_b']}", "op": "upsert",
                "updated_at": row["last_co_accessed_at"], "payload": {k: row[k] for k in row.keys()},
            })
    ppath = projects.projects_path()
    if ppath.exists():
        mtime = datetime.fromtimestamp(ppath.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if mtime > since:
            ops.append({
                "entity": "config", "entity_id": "projects", "op": "upsert",
                "updated_at": mtime, "payload": projects.load(),
            })
    return ops


# --- apply remote ops --------------------------------------------------------

def _local_fragment(conn, fid: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM fragments WHERE id = ?", (fid,)).fetchone()
    if row is None:
        return None
    local = {k: row[k] for k in row.keys()}
    local["tags"] = [r["tag"] for r in conn.execute("SELECT tag FROM fragment_tags WHERE fragment_id = ?", (fid,)).fetchall()]
    return local


def _write_fragment(conn, merged: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO fragments (id, content, summary, source_type, source_ref, confidence, accessed,
                               last_accessed_at, created_at, updated_at, pinned, below_threshold_since, project)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            content = excluded.content, summary = excluded.summary, source_type = excluded.source_type,
            source_ref = excluded.source_ref, confidence = excluded.confidence, accessed = excluded.accessed,
            last_accessed_at = excluded.last_accessed_at, created_at = excluded.created_at,
            updated_at = excluded.updated_at, pinned = excluded.pinned,
            below_threshold_since = excluded.below_threshold_since, project = excluded.project
        """,
        (
            merged["id"], merged.get("content") or "", merged.get("summary") or "",
            merged.get("source_type") or "manual", merged.get("source_ref"),
            float(merged.get("confidence") or 0.0), int(merged.get("accessed") or 0),
            merged.get("last_accessed_at"), merged.get("created_at") or _now(), merged.get("updated_at") or _now(),
            1 if merged.get("pinned") else 0, merged.get("below_threshold_since"), merged.get("project"),
        ),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO fragment_tags(fragment_id, tag) VALUES (?, ?)",
        [(merged["id"], t) for t in merged.get("tags") or []],
    )


def _apply_embedding(conn, fid: str, emb: dict[str, Any] | None) -> None:
    if not emb:
        return
    if emb.get("model") != config.get_setting("embedding_model"):
        return
    blob = base64.b64decode(emb["vector_b64"])
    conn.execute(
        """
        INSERT INTO fragment_embeddings (fragment_id, vector, dim, model) VALUES (?, ?, ?, ?)
        ON CONFLICT(fragment_id) DO UPDATE SET vector = excluded.vector, dim = excluded.dim, model = excluded.model
        """,
        (fid, blob, int(emb["dim"]), emb["model"]),
    )


def apply_ops(ops: list[dict[str, Any]]) -> dict[str, int]:
    stats = {"fragments": 0, "deleted": 0, "associations": 0, "config": 0, "skipped": 0}
    with get_conn() as conn:
        for op in ops:
            entity, eid, payload = op.get("entity"), op.get("entity_id"), op.get("payload") or {}
            if entity == "fragment":
                if conn.execute("SELECT 1 FROM fragment_tombstones WHERE fragment_id = ? AND deleted_at >= ?", (eid, payload.get("updated_at") or "")).fetchone():
                    stats["skipped"] += 1
                    continue
                local = _local_fragment(conn, eid)
                merged = merge.merge_fragment(local, payload)
                if not merge.changed(local, merged):
                    stats["skipped"] += 1
                    continue
                _write_fragment(conn, merged)
                _apply_embedding(conn, eid, payload.get("embedding"))
                stats["fragments"] += 1
            elif entity == "tombstone":
                local = _local_fragment(conn, eid)
                if merge.tombstone_wins(local, payload.get("deleted_at") or ""):
                    conn.execute("DELETE FROM fragments WHERE id = ?", (eid,))
                    stats["deleted"] += 1
                else:
                    stats["skipped"] += 1
            elif entity == "association":
                a, b = payload.get("fragment_a"), payload.get("fragment_b")
                if not a or not b:
                    continue
                if not conn.execute("SELECT 1 FROM fragments WHERE id IN (?, ?) GROUP BY 1 HAVING COUNT(*) = 2", (a, b)).fetchone():
                    stats["skipped"] += 1
                    continue
                row = conn.execute("SELECT * FROM associations WHERE fragment_a = ? AND fragment_b = ?", (a, b)).fetchone()
                merged = merge.merge_association({k: row[k] for k in row.keys()} if row else None, payload)
                conn.execute(
                    """
                    INSERT INTO associations (fragment_a, fragment_b, weight, co_accessed_count, last_co_accessed_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(fragment_a, fragment_b) DO UPDATE SET
                        weight = excluded.weight, co_accessed_count = excluded.co_accessed_count,
                        last_co_accessed_at = excluded.last_co_accessed_at
                    """,
                    (a, b, merged["weight"], merged["co_accessed_count"], merged["last_co_accessed_at"] or _now()),
                )
                stats["associations"] += 1
            elif entity == "config" and eid == "projects":
                if isinstance(payload, dict) and payload:
                    local_rules = projects.load()
                    for name, rule in payload.items():
                        mine = local_rules.setdefault(name, {"remotes": [], "paths": [], "aliases": []})
                        for key in ("remotes", "paths", "aliases"):
                            for v in rule.get(key, []) or []:
                                if v not in mine[key]:
                                    mine[key].append(v)
                    projects.save(local_rules)
                    stats["config"] += 1
    return stats


# --- push / pull -------------------------------------------------------------

def push(transport: Transport | None = None) -> dict[str, Any]:
    transport = transport or configured_transport()
    dev = device_id()
    since = get_state("last_push_watermark") or ""
    ops = collect_ops(since)
    sent = 0
    high = since
    for i in range(0, len(ops), BATCH):
        batch = ops[i:i + BATCH]
        transport("POST", "/v1/push", {"device": dev, "ops": batch})
        sent += len(batch)
        high = max([high, *(op["updated_at"] or "" for op in batch)])
    if sent:
        set_state("last_push_watermark", high)
    return {"pushed": sent, "watermark": high}


def pull(transport: Transport | None = None) -> dict[str, Any]:
    transport = transport or configured_transport()
    dev = device_id()
    since = int(get_state("last_pull_seq") or 0)
    totals = {"fragments": 0, "deleted": 0, "associations": 0, "config": 0, "skipped": 0}
    received = 0
    while True:
        resp = transport("GET", f"/v1/pull?since={since}&device={dev}", None)
        ops = resp.get("ops") or []
        if not ops:
            break
        stats = apply_ops(ops)
        for k in totals:
            totals[k] += stats.get(k, 0)
        received += len(ops)
        since = int(resp.get("next_seq") or since)
        set_state("last_pull_seq", str(since))
        if not resp.get("more"):
            break
    return {"received": received, "next_seq": since, **totals}


def sync(transport: Transport | None = None, *, quiet: bool = False) -> dict[str, Any]:
    """Push then pull. Records last_ok_at / last_error in sync_state."""
    try:
        out = {"device": device_id(), "push": push(transport), "pull": pull(transport), "ok": True}
        set_state("last_ok_at", _now())
        set_state("last_error", None)
        return out
    except SyncError as exc:
        set_state("last_error", f"{_now()} {exc}")
        if quiet:
            return {"ok": False, "error": str(exc)}
        raise


def status() -> dict[str, Any]:
    pending = len(collect_ops(get_state("last_push_watermark") or "")) if config.get_setting("sync_enabled") else None
    return {
        "enabled": bool(config.get_setting("sync_enabled")),
        "url": config.get_setting("sync_url"),
        "device": device_id() if (config.HIPPOCAMPUS_HOME / "device_id").exists() else None,
        "last_ok_at": get_state("last_ok_at"),
        "last_error": get_state("last_error"),
        "last_push_watermark": get_state("last_push_watermark"),
        "last_pull_seq": int(get_state("last_pull_seq") or 0),
        "pending_ops": pending,
    }
