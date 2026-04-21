"""SQLite vector store — one row per fragment."""

from __future__ import annotations

import struct
from typing import Iterable

from hippocampus.storage.db import get_conn, get_ro_conn


def pack(vector: list[float] | tuple[float, ...]) -> bytes:
    """Pack a float vector as little-endian float32 bytes."""
    n = len(vector)
    return struct.pack(f"<{n}f", *vector)


def unpack(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"<{dim}f", blob))


def put(fragment_id: str, vector: list[float], *, model: str) -> None:
    dim = len(vector)
    blob = pack(vector)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO fragment_embeddings (fragment_id, vector, dim, model)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(fragment_id) DO UPDATE SET
                vector = excluded.vector,
                dim = excluded.dim,
                model = excluded.model,
                created_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (fragment_id, blob, dim, model),
        )


def get(fragment_id: str) -> tuple[list[float], str] | None:
    with get_ro_conn() as conn:
        row = conn.execute(
            "SELECT vector, dim, model FROM fragment_embeddings WHERE fragment_id = ?",
            (fragment_id,),
        ).fetchone()
    if row is None:
        return None
    return unpack(row["vector"], int(row["dim"])), row["model"]


def iter_all(model: str | None = None) -> Iterable[tuple[str, list[float], str]]:
    """Yield (fragment_id, vector, model) for every stored embedding."""
    query = "SELECT fragment_id, vector, dim, model FROM fragment_embeddings"
    params: tuple = ()
    if model is not None:
        query += " WHERE model = ?"
        params = (model,)
    with get_ro_conn() as conn:
        for row in conn.execute(query, params).fetchall():
            yield row["fragment_id"], unpack(row["vector"], int(row["dim"])), row["model"]


def count(model: str | None = None) -> int:
    query = "SELECT COUNT(*) AS n FROM fragment_embeddings"
    params: tuple = ()
    if model is not None:
        query += " WHERE model = ?"
        params = (model,)
    with get_ro_conn() as conn:
        return int(conn.execute(query, params).fetchone()["n"])


def missing_ids(model: str | None = None) -> list[str]:
    """Fragment ids without an embedding (or with a different model)."""
    if model is None:
        with get_ro_conn() as conn:
            rows = conn.execute(
                """
                SELECT f.id FROM fragments f
                LEFT JOIN fragment_embeddings e ON e.fragment_id = f.id
                WHERE e.fragment_id IS NULL
                """
            ).fetchall()
    else:
        with get_ro_conn() as conn:
            rows = conn.execute(
                """
                SELECT f.id FROM fragments f
                LEFT JOIN fragment_embeddings e
                    ON e.fragment_id = f.id AND e.model = ?
                WHERE e.fragment_id IS NULL
                """,
                (model,),
            ).fetchall()
    return [r["id"] for r in rows]


def delete(fragment_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM fragment_embeddings WHERE fragment_id = ?", (fragment_id,))
