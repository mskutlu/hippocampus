"""Shared test fixtures.

All tests run against a fresh SQLite DB in a tmp_path; the Obsidian mirror is
also redirected to tmp_path so nothing leaks to ~/hippocampus-vault. Fixtures reset
the fragment-storage mirror hooks between tests.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def hippo_env(tmp_path, monkeypatch):
    """Isolate every test to its own ~/.hippocampus + vault."""
    home = tmp_path / ".hippocampus"
    vault = tmp_path / "vault"
    home.mkdir(parents=True, exist_ok=True)
    vault.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HIPPOCAMPUS_HOME", str(home))
    monkeypatch.setenv("HIPPOCAMPUS_VAULT", str(vault))
    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "pytest")

    # Force-reload modules that cached paths at import time.
    for mod_name in list(sys.modules):
        if mod_name.startswith("hippocampus"):
            sys.modules.pop(mod_name)

    from hippocampus import config
    importlib.reload(config)
    config.ensure_dirs()

    from hippocampus.storage import db as sdb
    sdb.init_db(config.DB_PATH)

    from hippocampus.sync import obsidian_mirror
    obsidian_mirror.bootstrap_hooks()

    yield {
        "home": home,
        "vault": vault,
        "fragments_dir": vault / "Fragments",
        "db_path": config.DB_PATH,
    }
