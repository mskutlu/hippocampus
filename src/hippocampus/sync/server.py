"""Self-hosted sync endpoint (V11): a dumb, append-only oplog.

    hippo sync serve --port 7879        # run on one always-on machine

Auth is one static bearer token (`sync_server_token` setting or
HIPPOCAMPUS_SYNC_TOKEN). The server speaks plain HTTP; put it behind
Tailscale or a TLS reverse proxy. Requires the `web` extra (FastAPI).
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hippocampus import config

try:
    from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
    import uvicorn

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False

PULL_CAP = 500
MAX_OPS_PER_PUSH = 1000


def oplog_path() -> Path:
    return Path(os.environ.get("HIPPOCAMPUS_SYNC_DB") or (config.HIPPOCAMPUS_HOME / "sync.db"))


def server_token() -> str:
    token = (os.environ.get("HIPPOCAMPUS_SYNC_TOKEN") or config.get_setting("sync_server_token") or "").strip()
    if not token:
        raise RuntimeError("no sync token: run `hippo sync token` or set HIPPOCAMPUS_SYNC_TOKEN")
    return token


def generate_token() -> str:
    token = secrets.token_urlsafe(32)
    config.set_setting("sync_server_token", token)
    return token


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops (
            seq         INTEGER PRIMARY KEY AUTOINCREMENT,
            device      TEXT NOT NULL,
            entity      TEXT NOT NULL,
            entity_id   TEXT NOT NULL,
            op          TEXT NOT NULL,
            payload     TEXT NOT NULL,
            updated_at  TEXT,
            received_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_device_seq ON ops(device, seq)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS devices (
            device        TEXT PRIMARY KEY,
            last_push_at  TEXT,
            last_pull_at  TEXT,
            last_pull_seq INTEGER
        )
        """
    )
    return conn


def _touch_device(conn: sqlite3.Connection, device: str, *, push: bool = False, pull_seq: int | None = None) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute("INSERT OR IGNORE INTO devices(device) VALUES (?)", (device,))
    if push:
        conn.execute("UPDATE devices SET last_push_at = ? WHERE device = ?", (now, device))
    if pull_seq is not None:
        conn.execute("UPDATE devices SET last_pull_at = ?, last_pull_seq = ? WHERE device = ?", (now, pull_seq, device))


def backfill_devices(path: Path) -> None:
    """Seed the devices table from the oplog for servers created before it existed."""
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO devices(device, last_push_at)
            SELECT device, MAX(received_at) FROM ops GROUP BY device
            """
        )


def list_devices(path: Path) -> list[dict[str, Any]]:
    with _connect(path) as conn:
        rows = conn.execute("SELECT * FROM devices ORDER BY device").fetchall()
    return [dict(r) for r in rows]


def append_ops(path: Path, device: str, ops: list[dict[str, Any]]) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with _connect(path) as conn:
        conn.executemany(
            "INSERT INTO ops(device, entity, entity_id, op, payload, updated_at, received_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (device, str(o.get("entity")), str(o.get("entity_id")), str(o.get("op")),
                 json.dumps(o.get("payload") or {}, ensure_ascii=False), o.get("updated_at"), now)
                for o in ops
            ],
        )
        _touch_device(conn, device, push=True)
        seq = conn.execute("SELECT MAX(seq) AS s FROM ops").fetchone()["s"]
    return int(seq or 0)


def read_ops(path: Path, since: int, exclude_device: str, limit: int = PULL_CAP) -> tuple[list[dict[str, Any]], int, bool]:
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT seq, device, entity, entity_id, op, payload, updated_at FROM ops WHERE seq > ? AND device != ? ORDER BY seq LIMIT ?",
            (since, exclude_device, limit + 1),
        ).fetchall()
        head = conn.execute("SELECT MAX(seq) AS s FROM ops").fetchone()["s"] or 0
        more = len(rows) > limit
        rows = rows[:limit]
        ops = [
            {"seq": r["seq"], "device": r["device"], "entity": r["entity"], "entity_id": r["entity_id"],
             "op": r["op"], "updated_at": r["updated_at"], "payload": json.loads(r["payload"])}
            for r in rows
        ]
        next_seq = ops[-1]["seq"] if ops else (since if more else int(head))
        _touch_device(conn, exclude_device, pull_seq=int(next_seq))
    return ops, int(next_seq), more


def create_app(*, token: str | None = None, db_path: Path | None = None) -> "FastAPI":
    if not FASTAPI_AVAILABLE:
        raise RuntimeError("sync server needs the web extra: uv pip install -e '.[web]'")
    expected = token or server_token()
    path = db_path or oplog_path()
    backfill_devices(path)
    app = FastAPI(title="Hippocampus sync", docs_url=None, redoc_url=None)

    def auth(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not secrets.compare_digest(authorization, f"Bearer {expected}"):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/v1/health")
    def health() -> dict:
        with _connect(path) as conn:
            head = conn.execute("SELECT MAX(seq) AS s FROM ops").fetchone()["s"] or 0
        devices = list_devices(path)
        return {
            "ok": True,
            "head_seq": int(head),
            "devices": len(devices),
            "device_status": [
                {**d, "behind": int(head) - int(d.get("last_pull_seq") or 0)} for d in devices
            ],
        }

    @app.post("/v1/push", dependencies=[Depends(auth)])
    def push(body: dict = Body(...)) -> dict:
        device = str(body.get("device") or "").strip()
        ops = body.get("ops") or []
        if not device or not isinstance(ops, list):
            raise HTTPException(status_code=400, detail="device and ops required")
        if len(ops) > MAX_OPS_PER_PUSH:
            raise HTTPException(status_code=413, detail="too many ops")
        seq = append_ops(path, device, ops) if ops else 0
        return {"accepted": len(ops), "seq": seq}

    @app.get("/v1/pull", dependencies=[Depends(auth)])
    def pull(since: int = Query(default=0, ge=0), device: str = Query(...)) -> dict:
        ops, next_seq, more = read_ops(path, since, device)
        return {"ops": ops, "next_seq": next_seq, "more": more}

    @app.get("/v1/config", dependencies=[Depends(auth)])
    def get_config() -> dict:
        with _connect(path) as conn:
            row = conn.execute(
                "SELECT payload FROM ops WHERE entity = 'config' AND entity_id = 'projects' ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        return {"projects": json.loads(row["payload"]) if row else {}}

    return app


def run(host: str = "127.0.0.1", port: int = 7879) -> None:
    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")
