"""Simulate the evidence-model rebase on a copy of the live DB. Never writes to the live DB."""

from __future__ import annotations

import os
import shutil
import sqlite3
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_DB = Path(os.environ.get("SIM_SOURCE_DB", Path.home() / ".hippocampus" / "hippocampus.db"))
LESSON_TAGS = {"gotchas", "gotcha", "preference", "lesson", "rule"}
LESSON_SOURCES = {"lesson", "user-preference", "correction"}
SUMMARY_SOURCES = {"session-summary", "session"}
SNIPPET = 80
SAMPLE_HITS = 60


def isolate() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="hippo-sim-"))
    os.environ["HIPPOCAMPUS_HOME"] = str(tmp / "home")
    os.environ["HIPPOCAMPUS_VAULT"] = str(tmp / "vault")
    os.environ["HOME"] = str(tmp / "userhome")
    os.environ["HIPPO_EMBEDDING_PROVIDER"] = "disabled"
    sys.path.insert(0, str(ROOT / "src"))
    return tmp


def copy_live(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def classify(source_type: str, tags: set[str]) -> str:
    if tags & LESSON_TAGS or source_type in LESSON_SOURCES:
        return "lessons"
    if source_type in SUMMARY_SOURCES:
        return "summaries"
    return "facts"


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else 0.0


def load_classes(conn: sqlite3.Connection) -> dict[str, str]:
    tags: dict[str, set[str]] = {}
    for fid, tag in conn.execute("SELECT fragment_id, tag FROM fragment_tags"):
        tags.setdefault(fid, set()).add(tag)
    return {
        fid: classify(src, tags.get(fid, set()))
        for fid, src in conn.execute("SELECT id, source_type FROM fragments")
    }


def pin_origin_counts(conn: sqlite3.Connection) -> dict[str, int]:
    out = {"manual": 0, "auto": 0, "unknown": 0}
    for (fid,) in conn.execute("SELECT id FROM fragments WHERE pinned = 1").fetchall():
        last = {
            kind: conn.execute(
                "SELECT MAX(id) FROM feedback_log WHERE fragment_id = ? AND kind = ?", (fid, kind)
            ).fetchone()[0]
            for kind in ("pin", "auto-pin")
        }
        if last["pin"] and (not last["auto-pin"] or last["pin"] > last["auto-pin"]):
            out["manual"] += 1
        elif last["auto-pin"]:
            out["auto"] += 1
        else:
            out["unknown"] += 1
    return out


def top_lists(conn: sqlite3.Connection, classes: dict[str, str]) -> dict[str, list[str]]:
    from hippocampus.dynamics import ranking

    projects = [
        r[0]
        for r in conn.execute(
            "SELECT project FROM fragments WHERE project IS NOT NULL GROUP BY project "
            "ORDER BY COUNT(*) DESC LIMIT 3"
        )
    ]
    lists = {"global": [f.id for f in ranking.top_n(15, scope="global")]}
    for p in projects:
        lists[f"project:{p}"] = [f.id for f in ranking.top_n(15, project=p, scope="project")]
    return lists


def snapshot(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    classes = load_classes(conn)
    buckets: dict[str, list[float]] = {"lessons": [], "summaries": [], "facts": []}
    for fid, conf in conn.execute("SELECT id, confidence FROM fragments"):
        buckets[classes[fid]].append(float(conf))
    stats = {
        k: {
            "n": len(v),
            "mean": round(statistics.fmean(v), 3) if v else 0.0,
            "p50": round(percentile(v, 0.5), 3),
            "p90": round(percentile(v, 0.9), 3),
        }
        for k, v in buckets.items()
    }
    snap = {"stats": stats, "pins": pin_origin_counts(conn), "tops": top_lists(conn, classes), "classes": classes}
    conn.close()
    return snap


def print_snapshot(title: str, snap: dict) -> None:
    print(f"\n== {title} ==")
    for cls, s in snap["stats"].items():
        print(f"{cls:10s} n={s['n']:5d} mean={s['mean']:.3f} p50={s['p50']:.3f} p90={s['p90']:.3f}")
    print("pins by origin:", snap["pins"])
    for name, ids in snap["tops"].items():
        mix: dict[str, int] = {}
        for fid in ids:
            mix[snap["classes"][fid]] = mix.get(snap["classes"][fid], 0) + 1
        print(f"top15 {name}: mix={mix}")
        print("   ids:", " ".join(ids))


def correction_hits(conn: sqlite3.Connection) -> tuple[int, int, int, int, list[str]]:
    from hippocampus.dynamics.negation import looks_like_correction

    rows = conn.execute("SELECT id, session_id, kind, content FROM session_ledger ORDER BY session_id, id").fetchall()
    asks = hits = asks_after_done = hits_after_done = 0
    samples: list[str] = []
    prev_session, prev_kind = None, None
    for r in rows:
        if r["kind"] == "ask":
            after_done = r["session_id"] == prev_session and prev_kind == "done"
            hit = looks_like_correction(r["content"])
            asks += 1
            asks_after_done += after_done
            hits += hit
            hits_after_done += hit and after_done
            if hit and len(samples) < SAMPLE_HITS:
                samples.append(" ".join(r["content"].split())[:SNIPPET])
        prev_session, prev_kind = r["session_id"], r["kind"]
    return asks, hits, asks_after_done, hits_after_done, samples


def replay_estimate(conn: sqlite3.Connection, classes: dict[str, str]) -> dict[str, dict[str, int]]:
    est = {c: {"helped_weak": 0, "helped_cite": 0, "hurt_negative": 0} for c in classes.values()}
    est.update({c: est.get(c, {"helped_weak": 0, "helped_cite": 0, "hurt_negative": 0}) for c in ("lessons", "summaries", "facts")})
    done_sessions = {
        r[0]: r[1]
        for r in conn.execute("SELECT session_id, MAX(created_at) FROM session_ledger WHERE kind = 'done' GROUP BY session_id")
    }
    for r in conn.execute("SELECT session_id, fragment_id, accessed_at FROM session_accesses WHERE via = 'recall'"):
        last = done_sessions.get(r["session_id"])
        if last and r["accessed_at"] <= last and r["fragment_id"] in classes:
            est[classes[r["fragment_id"]]]["helped_weak"] += 1
    import re

    cite = re.compile(r"frag_[0-9A-Z]{26}")
    for (content, details) in conn.execute("SELECT content, COALESCE(details, '') FROM session_ledger WHERE kind = 'done'"):
        for fid in set(cite.findall(f"{content} {details}")):
            if fid in classes:
                est[classes[fid]]["helped_cite"] += 1
    for (fid,) in conn.execute("SELECT fragment_id FROM feedback_log WHERE kind = 'negative'"):
        if fid in classes:
            est[classes[fid]]["hurt_negative"] += 1
    from hippocampus.dynamics.negation import looks_like_correction

    rows = conn.execute("SELECT id, session_id, kind, content, created_at FROM session_ledger ORDER BY session_id, id").fetchall()
    last_ask: dict[str, str] = {}
    for r in rows:
        if r["kind"] != "ask":
            continue
        since = last_ask.get(r["session_id"], "")
        last_ask[r["session_id"]] = r["created_at"]
        if not looks_like_correction(r["content"]):
            continue
        for (fid,) in conn.execute(
            "SELECT fragment_id FROM session_accesses WHERE session_id = ? AND via = 'recall' AND accessed_at >= ?",
            (r["session_id"], since),
        ):
            if fid in classes:
                est[classes[fid]]["hurt_correction_targets"] = est[classes[fid]].get("hurt_correction_targets", 0) + 1
    return est


def main() -> None:
    tmp = isolate()
    db_copy = tmp / "home" / "hippocampus.db"
    copy_live(db_copy)

    import hippocampus
    from hippocampus import config
    from hippocampus.dynamics import rebase
    from hippocampus.storage import db

    assert Path(hippocampus.__file__).resolve().is_relative_to(ROOT / "src"), hippocampus.__file__
    assert Path(config.DB_PATH).resolve() == db_copy.resolve(), config.DB_PATH
    db.init_db(db_copy)

    before = snapshot(db_copy)
    print_snapshot("BEFORE rebase", before)
    print("\ndry-run:", rebase.dry_run())
    print("apply:", rebase.apply())
    after = snapshot(db_copy)
    print_snapshot("AFTER rebase", after)

    for name, ids in after["tops"].items():
        gone = [i for i in before["tops"].get(name, []) if i not in ids]
        print(f"top15 {name}: changed={len(gone)} of {len(ids)}")

    conn = sqlite3.connect(db_copy)
    conn.row_factory = sqlite3.Row
    asks, hits, after_done, hits_after_done, samples = correction_hits(conn)
    print(f"\ncorrection detector: {hits} hits of {asks} asks; {hits_after_done} of {after_done} after a done")
    for s in samples:
        print("  -", s)
    print("replay estimate per class:", replay_estimate(conn, after["classes"]))
    conn.close()
    if "--keep" in sys.argv:
        print(f"\ncopy kept at {db_copy}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
