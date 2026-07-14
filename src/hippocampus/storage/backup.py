"""Consistent SQLite backup and restore helpers."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from hippocampus import config


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def verify(path: Path) -> None:
    with closing(_connect(path)) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        detail = result[0] if result else "no result"
        raise RuntimeError(f"SQLite integrity check failed: {detail}")


def prune(directory: Path, retention: int) -> list[str]:
    if retention < 1:
        return []
    candidates = sorted(
        [
            *directory.glob("hippocampus-*.db"),
            *directory.glob("pre-restore-*.db"),
            *directory.glob("pre-migration-*.db"),
        ],
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    removed: list[str] = []
    for path in candidates[retention:]:
        path.unlink()
        removed.append(str(path))
    return removed


def create(
    *,
    source: Path | None = None,
    directory: Path | None = None,
    retention: int | None = None,
    prefix: str = "hippocampus",
) -> dict:
    source_path = Path(source or config.DB_PATH)
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    backup_dir = Path(directory or config.BACKUPS_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_dir.chmod(0o700)
    target_path = backup_dir / f"{prefix}-{_timestamp()}.db"

    with closing(_connect(source_path)) as source_conn, closing(_connect(target_path)) as target_conn:
        source_conn.backup(target_conn)
    target_path.chmod(0o600)
    verify(target_path)

    keep = retention
    if keep is None:
        keep = int(config.get_setting("backup_retention_count") or 14)
    removed = prune(backup_dir, keep)
    return {
        "path": str(target_path),
        "bytes": target_path.stat().st_size,
        "integrity": "ok",
        "retention": keep,
        "removed": removed,
    }


def restore(
    backup_path: Path,
    *,
    destination: Path | None = None,
    safety_backup: bool = True,
) -> dict:
    source_path = Path(backup_path)
    destination_path = Path(destination or config.DB_PATH)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    verify(source_path)

    safety_path = None
    if safety_backup and destination_path.exists():
        safety_path = create(source=destination_path, prefix="pre-restore")["path"]

    destination_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(_connect(source_path)) as source_conn, closing(_connect(destination_path)) as destination_conn:
        source_conn.backup(destination_conn)
    destination_path.chmod(0o600)
    verify(destination_path)
    return {
        "restored_from": str(source_path),
        "destination": str(destination_path),
        "integrity": "ok",
        "safety_backup": safety_path,
    }
