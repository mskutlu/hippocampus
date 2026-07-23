"""Association edge store.

Associations are undirected. We enforce a canonical ordering (fragment_a <
fragment_b as text) so (A,B) and (B,A) collapse to a single row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from hippocampus.storage.db import get_conn, get_ro_conn


def _canonical(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def strengthen(a: str, b: str, weight_delta: float = 1.0) -> None:
    """Increment co-access count and weight for pair (a, b). Idempotent."""
    if a == b:
        return
    lo, hi = _canonical(a, b)
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO associations (fragment_a, fragment_b, weight, co_accessed_count, last_co_accessed_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(fragment_a, fragment_b) DO UPDATE SET
                weight = weight + excluded.weight,
                co_accessed_count = co_accessed_count + 1,
                last_co_accessed_at = excluded.last_co_accessed_at
            """,
            (lo, hi, weight_delta, now),
        )


def strengthen_all(fragment_ids: Iterable[str]) -> int:
    """Strengthen every pair in the given list. Returns number of edges touched."""
    ids = list(dict.fromkeys(fragment_ids))  # dedupe, keep order
    if len(ids) < 2:
        return 0
    touched = 0
    now = _now()
    with get_conn() as conn:
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                lo, hi = _canonical(a, b)
                conn.execute(
                    """
                    INSERT INTO associations (fragment_a, fragment_b, weight, co_accessed_count, last_co_accessed_at)
                    VALUES (?, ?, 1.0, 1, ?)
                    ON CONFLICT(fragment_a, fragment_b) DO UPDATE SET
                        weight = weight + 1.0,
                        co_accessed_count = co_accessed_count + 1,
                        last_co_accessed_at = excluded.last_co_accessed_at
                    """,
                    (lo, hi, now),
                )
                touched += 1
    return touched


def get_associated(fragment_id: str, limit: int = 10) -> list[tuple[str, float, int]]:
    """Return [(other_id, weight, co_accessed_count)] sorted by weight desc."""
    with get_ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                CASE WHEN fragment_a = ? THEN fragment_b ELSE fragment_a END AS other,
                weight,
                co_accessed_count
            FROM associations
            WHERE fragment_a = ? OR fragment_b = ?
            ORDER BY weight DESC
            LIMIT ?
            """,
            (fragment_id, fragment_id, fragment_id, limit),
        ).fetchall()
    return [(r["other"], float(r["weight"]), int(r["co_accessed_count"])) for r in rows]


def neighbors_of(fragment_id: str, *, limit: int = 8, min_weight: float = 1.0) -> list[str]:
    """Return the fragment ids most strongly co-accessed with this one.

    Used by cluster-level boost (v1.6.0 C2): when one fragment is reinforced,
    its tightest neighbors get a smaller share so the network learns clusters.
    """
    return [other for other, w, _ in get_associated(fragment_id, limit=limit) if w >= min_weight]


def get_graph(
    *,
    min_weight: float = 5.0,
    tag: str | None = None,
    source_type: str | None = None,
    min_confidence: float = 0.0,
    pinned_only: bool = False,
    created_after: str | None = None,
    created_before: str | None = None,
) -> dict:
    conditions = ["f.confidence >= ?"]
    params: list[object] = [min_confidence]
    if tag:
        conditions.append(
            "EXISTS (SELECT 1 FROM fragment_tags ft WHERE ft.fragment_id = f.id AND ft.tag = ?)"
        )
        params.append(tag)
    if source_type:
        conditions.append("f.source_type = ?")
        params.append(source_type)
    if pinned_only:
        conditions.append("f.pinned = 1")
    if created_after:
        conditions.append("substr(f.created_at, 1, 10) >= ?")
        params.append(created_after)
    if created_before:
        conditions.append("substr(f.created_at, 1, 10) <= ?")
        params.append(created_before)

    selected = f"SELECT f.id FROM fragments f WHERE {' AND '.join(conditions)}"
    with get_ro_conn() as conn:
        rows = conn.execute(
            f"""
            WITH selected AS ({selected})
            SELECT f.id, substr(f.summary, 1, 240) AS summary, f.source_type, f.confidence, f.accessed,
                   f.pinned, f.created_at, t.tag
            FROM selected s
            JOIN fragments f ON f.id = s.id
            LEFT JOIN fragment_tags t ON t.fragment_id = f.id
            ORDER BY f.id, t.tag
            """,
            params,
        ).fetchall()
        edge_rows = conn.execute(
            f"""
            WITH selected AS ({selected})
            SELECT a.fragment_a, a.fragment_b, a.weight, a.co_accessed_count
            FROM associations a
            JOIN selected sa ON sa.id = a.fragment_a
            JOIN selected sb ON sb.id = a.fragment_b
            WHERE a.weight >= ?
            ORDER BY a.weight DESC
            """,
            [*params, min_weight],
        ).fetchall()

    nodes: list[dict] = []
    by_id: dict[str, dict] = {}
    for row in rows:
        node = by_id.get(row["id"])
        if node is None:
            node = {
                "id": row["id"],
                "summary": row["summary"],
                "source_type": row["source_type"],
                "confidence": round(float(row["confidence"]), 6),
                "accessed": int(row["accessed"]),
                "pinned": bool(row["pinned"]),
                "created_at": row["created_at"],
                "tags": [],
            }
            by_id[row["id"]] = node
            nodes.append(node)
        if row["tag"] is not None:
            node["tags"].append(row["tag"])

    edges = [
        {
            "source": row["fragment_a"],
            "target": row["fragment_b"],
            "weight": float(row["weight"]),
            "co_accessed_count": int(row["co_accessed_count"]),
        }
        for row in edge_rows
    ]
    connected = {endpoint for edge in edges for endpoint in (edge["source"], edge["target"])}
    return {
        "nodes": nodes,
        "edges": edges,
        "counts": {
            "nodes": len(nodes),
            "edges": len(edges),
            "connected": len(connected),
            "isolated": len(nodes) - len(connected),
        },
    }
