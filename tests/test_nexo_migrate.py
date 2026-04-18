"""Tests for nexo_migrate — Plan Consolidado F0.0."""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import nexo_migrate  # noqa: E402
from nexo_migrate import (  # noqa: E402
    apply_migration,
    bootstrap_f00,
    ensure_migrations_table,
    get_structure_version,
    is_applied,
)


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    monkeypatch.setenv("NEXO_DB_PATH", str(tmp_path / "nexo.db"))
    yield


def test_ensure_table_idempotent(tmp_path):
    db = tmp_path / "nexo.db"
    conn = sqlite3.connect(str(db))
    ensure_migrations_table(conn)
    ensure_migrations_table(conn)  # second call must not raise
    conn.close()


def test_is_applied_false_by_default():
    assert is_applied("F0.0") is False


def test_apply_migration_runs_fn_and_records():
    seen = {"called": 0}

    def fn(conn):
        seen["called"] += 1
        conn.execute("CREATE TABLE IF NOT EXISTS _t (x INTEGER)")

    r = apply_migration("F0.0", fn, notes="bootstrap")
    assert r == {"applied": True, "version": "F0.0", "notes": "bootstrap"}
    assert seen["called"] == 1
    assert is_applied("F0.0") is True


def test_apply_migration_idempotent():
    bootstrap_f00()
    again = bootstrap_f00()
    assert again["applied"] is False
    assert again["reason"] == "already_applied"


def test_apply_migration_writes_structure_version_file():
    bootstrap_f00()
    assert get_structure_version() == "F0.0"


def test_apply_migration_sets_NEXO_MIGRATING_while_running():
    captured = {"flag": None}

    def fn(conn):
        captured["flag"] = os.environ.get("NEXO_MIGRATING")

    apply_migration("F0.test-env", fn)
    assert captured["flag"] == "1"
    assert os.environ.get("NEXO_MIGRATING") is None  # cleaned up


def test_apply_migration_rolls_back_on_exception():
    def fn(conn):
        conn.execute("CREATE TABLE rolled (x INTEGER)")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        apply_migration("F0.explode", fn)

    assert is_applied("F0.explode") is False
    # No structure-version file should exist for the failed migration.
    assert get_structure_version() in ("", "F0.0")


def test_bootstrap_f00_convenience():
    r = bootstrap_f00()
    assert r["applied"] is True
    assert r["version"] == "F0.0"
    assert is_applied("F0.0") is True
