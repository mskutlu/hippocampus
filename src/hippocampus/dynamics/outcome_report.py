"""Summarise recorded outcomes (shadow or applied) for precision review."""

from __future__ import annotations

from typing import Any

from hippocampus.dynamics.outcomes import SHADOW_PREFIX
from hippocampus.storage.db import get_ro_conn

TOP_N = 20
SUMMARY_CHARS = 60
SNIPPET_CHARS = 80


def _kind(source: str) -> str:
    return source.removeprefix(SHADOW_PREFIX).split(":", 1)[0]


def build(since: str | None = None) -> dict[str, Any]:
    cutoff = since or ""
    with get_ro_conn() as conn:
        rows = conn.execute(
            "SELECT session_id, fragment_id, outcome, strength, source, delta FROM fragment_outcomes "
            "WHERE created_at >= ?",
            (cutoff,),
        ).fetchall()
        counts: dict[tuple[str, str, str, bool], list[float]] = {}
        net: dict[str, float] = {}
        asks: dict[str, str] = {}
        for r in rows:
            key = (r["outcome"], _kind(r["source"]), r["strength"], r["source"].startswith(SHADOW_PREFIX))
            bucket = counts.setdefault(key, [0, 0.0])
            bucket[0] += 1
            bucket[1] += float(r["delta"] or 0.0)
            net[r["fragment_id"]] = net.get(r["fragment_id"], 0.0) + float(r["delta"] or 0.0)
            kind = r["source"].removeprefix(SHADOW_PREFIX)
            if kind.startswith("correction:"):
                asks[kind.split(":", 1)[1]] = r["session_id"]
        top = sorted(net.items(), key=lambda kv: -abs(kv[1]))[:TOP_N]
        summaries = {
            r["id"]: r["summary"] or ""
            for r in conn.execute(
                f"SELECT id, summary FROM fragments WHERE id IN ({','.join('?' * len(top))})",
                [fid for fid, _ in top],
            )
        } if top else {}
        fired = []
        for ask_id in sorted(asks, key=int, reverse=True)[:TOP_N]:
            row = conn.execute("SELECT content FROM session_ledger WHERE id = ?", (int(ask_id),)).fetchone()
            if row:
                fired.append(" ".join(row["content"].split())[:SNIPPET_CHARS])
    return {
        "counts": [
            {"outcome": o, "source": s, "strength": st, "shadow": sh, "n": int(v[0]), "net_delta": round(v[1], 4)}
            for (o, s, st, sh), v in sorted(counts.items())
        ],
        "top_fragments": [
            {"id": fid, "net_delta": round(d, 4), "summary": summaries.get(fid, "")[:SUMMARY_CHARS]}
            for fid, d in top
        ],
        "recent_corrections": fired,
    }
