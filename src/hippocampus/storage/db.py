"""SQLite connection + migration runner.

All callers must go through `get_conn()` so that WAL mode and foreign keys are
enabled consistently. Connections are short-lived: open, run statements in a
transaction, close. This avoids thread/subprocess footguns with sqlite3.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from hippocampus import config

_PACKAGE_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
MIGRATIONS_DIR = (
    _PACKAGE_MIGRATIONS_DIR
    if _PACKAGE_MIGRATIONS_DIR.exists()
    else Path(__file__).resolve().parents[3] / "migrations"
)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")


@contextmanager
def get_conn(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a ready-to-use sqlite3 connection with pragmas applied.

    Commits on successful exit, rolls back on exception, always closes.
    """
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=5.0)
    config.secure_file(path)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
        config.secure_file(path)
        config.secure_file(Path(f"{path}-wal"))
        config.secure_file(Path(f"{path}-shm"))


@contextmanager
def get_ro_conn(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Read-only connection: no BEGIN/COMMIT overhead, no transaction."""
    path = Path(db_path or config.DB_PATH)
    conn = sqlite3.connect(path, timeout=5.0)
    config.secure_file(path)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    try:
        yield conn
    finally:
        conn.close()
        config.secure_file(path)
        config.secure_file(Path(f"{path}-wal"))
        config.secure_file(Path(f"{path}-shm"))


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    try:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        return {r["version"] for r in rows}
    except sqlite3.OperationalError:
        return set()  # table not yet created


def init_db(db_path: Path | None = None) -> None:
    """Apply all pending migrations in lexical order. Idempotent."""
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        raise RuntimeError(f"No migrations found in {MIGRATIONS_DIR}")

    # We use a non-transactional connection here because CREATE VIRTUAL TABLE
    # on fts5 does not play nice with a surrounding BEGIN on some builds.
    conn: sqlite3.Connection | None = sqlite3.connect(
        path,
        isolation_level=None,
        timeout=5.0,
    )
    config.secure_file(path)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    rollback_backup: Path | None = None
    try:
        applied = applied_versions(conn)
        pending = [
            migration
            for migration in migration_files
            if int(migration.name.split("_", 1)[0]) not in applied
        ]
        if applied and pending:
            from hippocampus.storage import backup

            rollback_backup = Path(
                backup.create(source=path, prefix="pre-migration")["path"]
            )
        for mf in pending:
            sql = mf.read_text(encoding="utf-8")
            conn.executescript(sql)
    except Exception:
        conn.close()
        conn = None
        if rollback_backup:
            from hippocampus.storage import backup

            backup.restore(
                rollback_backup,
                destination=path,
                safety_backup=False,
            )
        raise
    finally:
        if conn is not None:
            conn.close()
        config.secure_file(path)
        config.secure_file(Path(f"{path}-wal"))
        config.secure_file(Path(f"{path}-shm"))
