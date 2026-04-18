"""Plan Consolidado 0.3 — entities table extended with aliases/metadata/source/confidence/access_mode.

Verifies:
  - Fresh install CREATE TABLE has all new columns.
  - Legacy DB migrates via _m44_entities_extended_schema without data loss.
  - Defaults are applied correctly.
"""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def legacy_entities_conn(monkeypatch):
    """A sqlite connection seeded with the pre-migration entities schema."""
    tmpdir = tempfile.mkdtemp(prefix="nexo-entities-test-")
    db_path = Path(tmpdir) / "nexo.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'general',
            value TEXT NOT NULL,
            notes TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO entities (name, type, value, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("cloudflare", "host", "wazion.com", 1700000000.0, 1700000000.0),
    )
    conn.commit()
    yield conn
    conn.close()


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def test_legacy_schema_gets_new_columns(legacy_entities_conn):
    from db._schema import _m44_entities_extended_schema

    pre = _columns(legacy_entities_conn, "entities")
    assert "aliases" not in pre
    assert "metadata" not in pre
    assert "source" not in pre
    assert "confidence" not in pre
    assert "access_mode" not in pre

    _m44_entities_extended_schema(legacy_entities_conn)

    post = _columns(legacy_entities_conn, "entities")
    for col in ("aliases", "metadata", "source", "confidence", "access_mode"):
        assert col in post, f"{col} missing after migration"

    # Legacy rows survive
    rows = legacy_entities_conn.execute("SELECT * FROM entities").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["name"] == "cloudflare"
    assert row["type"] == "host"
    assert row["value"] == "wazion.com"
    assert row["aliases"] == "[]"
    assert row["metadata"] == "{}"
    assert row["source"] == "manual"
    assert row["confidence"] == 1.0
    assert row["access_mode"] == "unknown"


def test_migration_is_idempotent(legacy_entities_conn):
    from db._schema import _m44_entities_extended_schema

    _m44_entities_extended_schema(legacy_entities_conn)
    _m44_entities_extended_schema(legacy_entities_conn)  # second call = no-op
    post = _columns(legacy_entities_conn, "entities")
    # no duplicate columns, count = 7 original + 5 new
    assert len(post) == 12


def test_fresh_install_has_all_columns(tmp_path, monkeypatch):
    """Point NEXO_HOME to an empty dir and let init_db() build the schema from scratch."""
    home = tmp_path / "nexo-home"
    (home / "brain").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(home))

    # Force a clean import so init_db picks up the new NEXO_HOME
    import importlib
    import sys

    for mod in [m for m in list(sys.modules) if m == "db" or m.startswith("db.")]:
        sys.modules.pop(mod, None)

    from db._core import init_db, get_db

    init_db()
    conn = get_db()
    cols = _columns(conn, "entities")
    for col in ("aliases", "metadata", "source", "confidence", "access_mode"):
        assert col in cols, f"{col} missing in fresh install"
