"""Project scoping (V11).

A project is a name that groups fragments and sessions. It is resolved from
the working directory in this order:

  1. HIPPOCAMPUS_PROJECT env var
  2. a `.hippocampus-project` file in cwd or any ancestor
  3. `projects.json` remote rules matched against `git remote get-url origin`
  4. `projects.json` path rules matched against cwd and its ancestors
  5. None (global)

`projects.json` lives in HIPPOCAMPUS_HOME and is synced between devices:

    {"acme": {"remotes": ["gitlab.com/acme/*"],
                 "paths": ["~/work/acme-*"],
                 "aliases": ["acme-orders", "customer-a"]}}

Remotes are matched with scheme, credentials, and `.git` stripped. Paths are
globs expanded per device. Aliases are used only by `backfill` to map old
tags onto a project. The most specific (longest) matching pattern wins.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from hippocampus import config

MARKER_FILE = ".hippocampus-project"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_remote_cache: dict[str, str | None] = {}
_resolve_cache: dict[str, str | None] = {}


def projects_path() -> Path:
    return config.HIPPOCAMPUS_HOME / "projects.json"


def load() -> dict[str, dict[str, list[str]]]:
    path = projects_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out: dict[str, dict[str, list[str]]] = {}
    for name, rule in (raw or {}).items():
        if not isinstance(rule, dict):
            continue
        out[str(name)] = {
            "remotes": [str(x) for x in rule.get("remotes", []) or []],
            "paths": [str(x) for x in rule.get("paths", []) or []],
            "aliases": [str(x) for x in rule.get("aliases", []) or []],
        }
    return out


def save(data: dict[str, dict[str, list[str]]]) -> Path:
    config.ensure_dirs()
    path = projects_path()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    config.secure_file(path)
    _resolve_cache.clear()
    return path


def validate_name(name: str) -> str:
    name = (name or "").strip().lower()
    if not _NAME_RE.match(name):
        raise ValueError("project name must match [a-z0-9][a-z0-9._-]{0,63}")
    return name


def normalize_remote(url: str) -> str:
    """'https://user@gitlab.com/org/repo.git' -> 'gitlab.com/org/repo'."""
    u = (url or "").strip()
    u = re.sub(r"^[a-z+]+://", "", u)
    u = re.sub(r"^[^@/]+@", "", u)
    u = u.replace(":", "/", 1) if re.match(r"^[^/]+:[^/]", u) else u
    u = re.sub(r"\.git/?$", "", u)
    return u.strip("/").lower()


def git_remote(cwd: str | Path) -> str | None:
    key = str(cwd)
    if key in _remote_cache:
        return _remote_cache[key]
    remote: str | None = None
    try:
        out = subprocess.run(
            ["git", "-C", key, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            remote = normalize_remote(out.stdout)
    except Exception:
        remote = None
    _remote_cache[key] = remote
    return remote


def _expand(pattern: str) -> str:
    return os.path.expanduser(pattern).rstrip("/")


def _path_matches(path: Path, pattern: str) -> bool:
    pat = _expand(pattern)
    for candidate in (path, *path.parents):
        if fnmatch.fnmatchcase(str(candidate), pat):
            return True
    return False


def _best(matches: list[tuple[int, str]]) -> str | None:
    if not matches:
        return None
    matches.sort(key=lambda t: -t[0])
    return matches[0][1]


def match_remote(remote: str | None, rules: dict | None = None) -> str | None:
    if not remote:
        return None
    rules = load() if rules is None else rules
    hits = [
        (len(pat), name)
        for name, rule in rules.items()
        for pat in rule.get("remotes", [])
        if fnmatch.fnmatchcase(remote, pat.lower())
    ]
    return _best(hits)


def match_path(path: str | Path | None, rules: dict | None = None) -> str | None:
    if not path:
        return None
    rules = load() if rules is None else rules
    p = Path(path)
    hits = [
        (len(_expand(pat)), name)
        for name, rule in rules.items()
        for pat in rule.get("paths", [])
        if _path_matches(p, pat)
    ]
    return _best(hits)


def match_alias(token: str, rules: dict | None = None) -> str | None:
    """Map a tag or folder name onto a project via names and aliases."""
    rules = load() if rules is None else rules
    t = (token or "").strip().lower()
    if not t:
        return None
    if t in rules:
        return t
    hits = [
        (len(alias), name)
        for name, rule in rules.items()
        for alias in rule.get("aliases", [])
        if fnmatch.fnmatchcase(t, alias.lower())
    ]
    return _best(hits)


def marker_project(cwd: str | Path) -> str | None:
    p = Path(cwd)
    for candidate in (p, *p.parents):
        marker = candidate / MARKER_FILE
        if marker.is_file():
            try:
                return validate_name(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
    return None


def resolve(cwd: str | Path | None = None) -> str | None:
    """Resolve the project for `cwd` (default: HIPPOCAMPUS_CWD / cwd)."""
    env = (os.environ.get("HIPPOCAMPUS_PROJECT") or "").strip()
    if env:
        return validate_name(env)
    raw = cwd or os.environ.get("HIPPOCAMPUS_WORKSPACE") or os.environ.get("HIPPOCAMPUS_CWD") or os.getcwd()
    try:
        path = Path(raw).resolve()
    except OSError:
        path = Path(raw)
    key = str(path)
    if key in _resolve_cache:
        return _resolve_cache[key]
    if not path.exists():
        _resolve_cache[key] = None
        return None
    rules = load()
    found = marker_project(path)
    if found is None and rules:
        found = match_remote(git_remote(path), rules)
    if found is None and rules:
        found = match_path(path, rules)
    _resolve_cache[key] = found
    return found


def add(name: str, *, remotes: list[str] = (), paths: list[str] = (), aliases: list[str] = ()) -> dict[str, Any]:
    name = validate_name(name)
    data = load()
    rule = data.setdefault(name, {"remotes": [], "paths": [], "aliases": []})
    for key, values in (("remotes", remotes), ("paths", paths), ("aliases", aliases)):
        for v in values:
            v = v.strip()
            if v and v not in rule[key]:
                rule[key].append(v)
    save(data)
    return {name: rule}


def backfill(*, dry_run: bool = True) -> dict[str, Any]:
    """Assign a project to fragments that have none.

    Order: the source session's key (`cwd-<name>` / `ws-<name>`) matched as an
    alias, then the fragment's tags matched as aliases. Unmatched stay global.
    """
    from hippocampus.storage.db import get_conn, get_ro_conn

    rules = load()
    assigned: dict[str, int] = {}
    unmatched = 0
    updates: list[tuple[str, str]] = []
    with get_ro_conn() as conn:
        rows = conn.execute(
            """
            SELECT f.id, f.source_ref, s.session_key
            FROM fragments f
            LEFT JOIN sessions s ON s.id = f.source_ref
            WHERE f.project IS NULL
            """
        ).fetchall()
        tags_by_id: dict[str, list[str]] = {}
        for r in conn.execute("SELECT fragment_id, tag FROM fragment_tags").fetchall():
            tags_by_id.setdefault(r["fragment_id"], []).append(r["tag"])
    for row in rows:
        project: str | None = None
        key = row["session_key"] or ""
        m = re.search(r"(?:cwd|ws)-(.+?)-[0-9a-f]{16}$", key)
        if m:
            project = match_alias(m.group(1), rules)
        if project is None:
            for tag in tags_by_id.get(row["id"], []):
                project = match_alias(tag, rules)
                if project:
                    break
        if project is None:
            unmatched += 1
            continue
        assigned[project] = assigned.get(project, 0) + 1
        updates.append((project, row["id"]))
    if not dry_run and updates:
        with get_conn() as conn:
            conn.executemany("UPDATE fragments SET project = ? WHERE id = ?", updates)
    return {"dry_run": dry_run, "assigned": assigned, "unmatched": unmatched, "total": len(rows)}
