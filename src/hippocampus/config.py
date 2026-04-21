"""Centralised configuration for Hippocampus.

All paths and biological-model constants live here so tests can override them
via environment variables without editing code.

User-mutable settings (mode toggles, timers) are persisted in
`~/.hippocampus/config.json`. Env vars always win over the JSON file so tests
and ad-hoc overrides stay easy.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HOME = Path.home()

# Runtime state: DB, logs, backups, per-client session pointer files.
HIPPOCAMPUS_HOME = Path(os.environ.get("HIPPOCAMPUS_HOME", HOME / ".hippocampus"))

# Human-browsable mirror of every fragment as a markdown note.
# Default: ~/hippocampus-vault/ so the mirror lives in a dedicated directory.
# Users who already have an Obsidian vault (e.g. ~/my-vault/) can point
# Hippocampus at it by setting HIPPOCAMPUS_VAULT in their shell env:
#   export HIPPOCAMPUS_VAULT="$HOME/my-vault"
VAULT_HOME = Path(os.environ.get("HIPPOCAMPUS_VAULT", HOME / "hippocampus-vault"))
FRAGMENTS_DIR = VAULT_HOME / "Fragments"
FRAGMENTS_ARCHIVE_DIR = FRAGMENTS_DIR / ".archive"

DB_PATH = HIPPOCAMPUS_HOME / "hippocampus.db"
LOG_DIR = HIPPOCAMPUS_HOME / "logs"
BACKUPS_DIR = HIPPOCAMPUS_HOME / "backups"
SESSION_POINTER_DIR = HIPPOCAMPUS_HOME / "sessions"
INJECTION_FILE = HIPPOCAMPUS_HOME / "_HIPPOCAMPUS_CONTEXT.md"
EVENTS_LOG = LOG_DIR / "events.jsonl"

# ---------------------------------------------------------------------------
# Biological-model constants (defaults match the spec)
# ---------------------------------------------------------------------------

BOOST_DELTA: float = 0.015          # confidence += on recall / get_fragment
DECAY_DELTA: float = 0.002          # confidence -= per decay cycle if unused
FEEDBACK_DELTA: float = 0.02        # confidence -= on forget()
CONFIDENCE_INIT: float = 0.5        # new fragments
CONFIDENCE_MIN: float = 0.0
CONFIDENCE_MAX: float = 1.0

# Auto-prune threshold: below this for ARCHIVE_GRACE_DAYS and the fragment is
# archived (moved to vault/Fragments/.archive and removed from SQLite).
ARCHIVE_THRESHOLD: float = 0.05
ARCHIVE_GRACE_DAYS: int = 7

# Ranking: score = CONF*w_conf + recency*w_recency   (weights must sum to 1.0)
RECENCY_HALFLIFE_DAYS: float = 14.0
RANK_W_CONFIDENCE: float = 0.7
RANK_W_RECENCY: float = 0.3

# Injection defaults
TOP_N_DEFAULT: int = 15
INJECTION_SUMMARY_MAX_CHARS: int = 200

# Session semantics
SESSION_STALE_HOURS: int = 24       # auto-close sessions older than this

# Marker blocks for automatic injection into client rules files. Hippocampus
# owns everything between START and END; anything outside is preserved.
#
# There are two independent blocks:
#   * The "long-term" block renders top-N fragments (confidence × recency).
#   * The "working" block renders the current session's ledger (asks, dones,
#     decisions, blockers) so the AI keeps task context across compaction.
INJECTION_MARKER_START = "<!-- HIPPOCAMPUS:START -->"
INJECTION_MARKER_END = "<!-- HIPPOCAMPUS:END -->"

WORKING_MARKER_START = "<!-- HIPPOCAMPUS:WORKING:START -->"
WORKING_MARKER_END = "<!-- HIPPOCAMPUS:WORKING:END -->"

# Working-block rendering caps
WORKING_MAX_ASKS: int = 10
WORKING_MAX_DONES: int = 10
WORKING_MAX_NEXT: int = 5
WORKING_MAX_NOTES: int = 5
WORKING_CONTENT_MAX_CHARS: int = 220


def ensure_dirs() -> None:
    """Create all runtime directories if missing. Idempotent."""
    for p in (
        HIPPOCAMPUS_HOME,
        LOG_DIR,
        BACKUPS_DIR,
        SESSION_POINTER_DIR,
        FRAGMENTS_DIR,
        FRAGMENTS_ARCHIVE_DIR,
    ):
        p.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# User-mutable runtime settings (persisted in ~/.hippocampus/config.json)
# ---------------------------------------------------------------------------

_CONFIG_FILE = HIPPOCAMPUS_HOME / "config.json"

_DEFAULTS: dict[str, Any] = {
    "working_block_mode": "per_client",   # {"per_client", "shared"}
    "auto_end_idle_minutes": None,        # None disables; int => minutes
    "embedding_provider": "fastembed",    # fastembed | sentence-transformers
    "embedding_model": "BAAI/bge-small-en-v1.5",
    "embedding_truncate_dim": None,       # Matryoshka truncation (int) or None
    "embedding_trust_remote_code": True,  # needed for stella / gte-Qwen2 / etc
    "semantic_weight": 0.5,                # 0.0 = FTS only, 1.0 = semantic only
}


def _load_file_config() -> dict[str, Any]:
    if not _CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _env_override(key: str, default: Any) -> Any:
    """Map config key to env var name (HIPPO_<UPPER>) and coerce types."""
    env_key = "HIPPO_" + key.upper()
    raw = os.environ.get(env_key)
    if raw is None:
        return default
    if isinstance(default, bool):
        return raw.lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int) or default is None:
        try:
            return int(raw)
        except ValueError:
            return raw
    return raw


def get_setting(key: str) -> Any:
    """Return effective value: env var > config file > built-in default."""
    file_cfg = _load_file_config()
    default = _DEFAULTS.get(key)
    value = file_cfg.get(key, default)
    return _env_override(key, value)


def set_setting(key: str, value: Any) -> None:
    """Persist a setting in `config.json`. Unknown keys are allowed."""
    ensure_dirs()
    data = _load_file_config()
    data[key] = value
    _CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def all_settings() -> dict[str, Any]:
    """Effective settings (env + file + defaults) for `hippo config show`."""
    return {k: get_setting(k) for k in _DEFAULTS}


def config_path() -> Path:
    return _CONFIG_FILE
