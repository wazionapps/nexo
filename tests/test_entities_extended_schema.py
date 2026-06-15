"""Plan Consolidado 0.3 — entities table extended with aliases/metadata/source/confidence/access_mode.

Verifies:
  - Fresh install CREATE TABLE has all new columns.
  - Legacy DB migrates via _m44_entities_extended_schema without data loss.
  - Defaults are applied correctly.
"""

import os
import json
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

    # Force a re-resolution of NEXO_HOME by reloading the db stack IN PLACE.
    # Do NOT pop the ``db`` package object: that creates a NEW module object and
    # orphans the ``import db`` global of other already-collected test modules
    # (e.g. test_resolution_cache), leaking a stale connection into them — the
    # resolution_cache isolation flake. ``db/__init__`` reloads its submodules in
    # place, so reloading the package re-points DB_PATH coherently.
    import importlib
    import sys

    import db

    importlib.reload(db)
    from db._core import init_db, get_db

    init_db()
    conn = get_db()
    cols = _columns(conn, "entities")
    for col in ("aliases", "metadata", "source", "confidence", "access_mode"):
        assert col in cols, f"{col} missing in fresh install"


def test_entity_crud_uses_extended_columns(isolated_db):
    from db import create_entity, search_entities, update_entity

    entity_id = create_entity(
        name="Maria iMac",
        type="host",
        value=json.dumps({"hostname": "maria-imac"}),
        aliases=["equipo de maria", "maria-imac.local"],
        metadata={"privacy_level": "private"},
        source="manual",
        confidence=0.9,
        access_mode="read_only",
    )

    rows = search_entities("equipo maria")
    assert rows
    row = next(item for item in rows if item["id"] == entity_id)
    assert json.loads(row["aliases"]) == ["equipo de maria", "maria-imac.local"]
    assert json.loads(row["metadata"])["privacy_level"] == "private"
    assert row["access_mode"] == "read_only"
    assert row["source"] == "manual"
    assert row["confidence"] == 0.9

    update_entity(
        entity_id,
        aliases=["nora-host"],
        metadata={"privacy_level": "normal"},
        access_mode="read_write",
        confidence=0.7,
    )
    updated = next(item for item in search_entities("nora-host") if item["id"] == entity_id)
    assert json.loads(updated["aliases"]) == ["nora-host"]
    assert json.loads(updated["metadata"])["privacy_level"] == "normal"
    assert updated["access_mode"] == "read_write"
    assert updated["confidence"] == 0.7
