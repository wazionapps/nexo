"""Shared fixtures for NEXO test suite.

Uses isolated temp databases so tests never touch production data.
"""

import os
import sys
import sqlite3

import pytest

# Add parent dir to path so we can import nexo modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Redirect both nexo.db and cognitive.db to temp files per test."""
    test_db = str(tmp_path / "test_nexo.db")
    test_cog_db = str(tmp_path / "test_cognitive.db")

    monkeypatch.setenv("NEXO_TEST_DB", test_db)
    monkeypatch.setenv("NEXO_COGNITIVE_DB", test_cog_db)
    monkeypatch.setenv("NEXO_SKIP_FS_INDEX", "1")  # Don't spawn FTS background threads

    import db as db_mod
    import cognitive as cog_mod

    # Close existing connections completely
    db_mod.close_db()
    if cog_mod._conn is not None:
        try:
            cog_mod._conn.close()
        except Exception:
            pass
        cog_mod._conn = None

    # Point to temp paths
    db_mod.DB_PATH = test_db
    cog_mod.COGNITIVE_DB = test_cog_db

    # Create a fresh raw connection (bypass _SerializedConnection for init)
    raw = sqlite3.connect(test_db, timeout=30, check_same_thread=False,
                          isolation_level=None)
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA busy_timeout=30000")
    raw.execute("PRAGMA foreign_keys=ON")
    raw.row_factory = sqlite3.Row

    # Wrap it as the shared connection
    wrapped = db_mod._SerializedConnection(raw)
    db_mod._shared_conn = wrapped

    # Initialize schemas
    db_mod.init_db()
    db_mod.run_migrations()

    yield {
        "nexo_db": test_db,
        "cognitive_db": test_cog_db,
    }

    # Cleanup
    db_mod.close_db()
    if cog_mod._conn is not None:
        try:
            cog_mod._conn.close()
        except Exception:
            pass
        cog_mod._conn = None
