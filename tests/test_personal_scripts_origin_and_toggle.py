"""Plan Consolidado F0.1 — origin column in personal_scripts table."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest


LEGACY_CREATE = """
CREATE TABLE IF NOT EXISTS personal_scripts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    runtime TEXT DEFAULT 'unknown',
    metadata_json TEXT DEFAULT '{}',
    created_by TEXT DEFAULT 'manual',
    source TEXT DEFAULT 'filesystem',
    enabled INTEGER NOT NULL DEFAULT 1,
    has_inline_metadata INTEGER NOT NULL DEFAULT 0,
    last_run_at TEXT DEFAULT NULL,
    last_exit_code INTEGER DEFAULT NULL,
    last_synced_at TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def legacy_conn():
    tmpdir = tempfile.mkdtemp(prefix="nexo-origin-test-")
    conn = sqlite3.connect(Path(tmpdir) / "nexo.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(LEGACY_CREATE)
    conn.execute(
        "INSERT INTO personal_scripts (id, name, path) VALUES (?, ?, ?)",
        ("sc-1", "morning-agent", "/tmp/morning-agent.py"),
    )
    conn.commit()
    yield conn
    conn.close()


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def test_legacy_schema_gains_origin_column(legacy_conn):
    from db._schema import _m45_personal_scripts_origin

    assert "origin" not in _columns(legacy_conn, "personal_scripts")
    _m45_personal_scripts_origin(legacy_conn)
    cols = _columns(legacy_conn, "personal_scripts")
    assert "origin" in cols

    row = legacy_conn.execute("SELECT origin FROM personal_scripts").fetchone()
    assert row["origin"] == "user"


def test_migration_is_idempotent(legacy_conn):
    from db._schema import _m45_personal_scripts_origin

    _m45_personal_scripts_origin(legacy_conn)
    _m45_personal_scripts_origin(legacy_conn)
    cols = _columns(legacy_conn, "personal_scripts")
    assert cols.count("origin") == 1
