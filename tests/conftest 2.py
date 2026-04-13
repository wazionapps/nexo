"""Shared fixtures for NEXO test suite.

Uses isolated temp databases so tests never touch production data and forces
repo-local imports so the suite never silently picks up installed runtime code.
"""

import importlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
REPO_PREFIXES = (
    "plugins",
    "db",
    "doctor",
    "cognitive",
    "hook_guardrails",
    "script_registry",
    "state_watchers_runtime",
)


def _ensure_repo_src_first() -> None:
    """Keep the repo src/ directory first in sys.path for deterministic imports."""
    sys.path[:] = [path for path in sys.path if path != SRC]
    sys.path.insert(0, SRC)


_ensure_repo_src_first()


def _purge_external_repo_modules(prefixes: tuple[str, ...]) -> None:
    """Remove conflicting already-imported modules that do not come from this repo."""
    for name, module in list(sys.modules.items()):
        if not any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes):
            continue
        path = getattr(module, "__file__", None)
        if not path:
            continue
        try:
            resolved = Path(path).resolve()
        except Exception:
            continue
        if not resolved.is_relative_to(ROOT):
            sys.modules.pop(name, None)


def _reset_repo_import_state(prefixes: tuple[str, ...] = REPO_PREFIXES) -> None:
    """Force core package imports to come from this repo, not the live runtime."""
    _ensure_repo_src_first()
    _purge_external_repo_modules(prefixes)
    importlib.invalidate_caches()


_reset_repo_import_state()


@pytest.fixture(autouse=True)
def repo_import_isolation():
    """Prevent earlier tests from polluting imports with installed runtime modules."""
    _reset_repo_import_state()
    yield
    _reset_repo_import_state()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Redirect both nexo.db and cognitive.db to temp files per test."""
    test_db = str(tmp_path / "test_nexo.db")
    test_cog_db = str(tmp_path / "test_cognitive.db")

    monkeypatch.setenv("NEXO_TEST_DB", test_db)
    monkeypatch.setenv("NEXO_COGNITIVE_DB", test_cog_db)
    monkeypatch.setenv("NEXO_SKIP_FS_INDEX", "1")

    import db._core as db_core
    import cognitive._core as cog_core

    # Close existing connections
    db_core.close_db()
    if cog_core._conn is not None:
        try:
            cog_core._conn.close()
        except Exception:
            pass
        cog_core._conn = None

    # Point to temp paths
    db_core.DB_PATH = test_db
    cog_core.COGNITIVE_DB = test_cog_db

    # Create a fresh raw connection
    raw = sqlite3.connect(test_db, timeout=30, check_same_thread=False,
                          isolation_level=None)
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA busy_timeout=30000")
    raw.execute("PRAGMA foreign_keys=ON")
    raw.row_factory = sqlite3.Row

    wrapped = db_core._SerializedConnection(raw)
    db_core._shared_conn = wrapped

    # Initialize schemas
    from db._core import init_db
    from db._schema import run_migrations
    init_db()
    run_migrations()

    yield {
        "nexo_db": test_db,
        "cognitive_db": test_cog_db,
    }

    # Cleanup
    db_core.close_db()
    if cog_core._conn is not None:
        try:
            cog_core._conn.close()
        except Exception:
            pass
        cog_core._conn = None
