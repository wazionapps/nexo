"""v6.0.1 — migration m42 is idempotent and produces the expected shape.

Runs the full migration set twice against a scratch DB and asserts that
the new column/table land exactly once.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    import db._core as _core

    tmp_db = str(tmp_path / "nexo.db")
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    monkeypatch.setenv("NEXO_DB", tmp_db)
    monkeypatch.setenv("NEXO_TEST_DB", tmp_db)
    monkeypatch.setattr(_core, "DB_PATH", tmp_db, raising=False)
    monkeypatch.setattr(_core, "_shared_conn", None, raising=False)

    import db as db_pkg
    db_pkg.init_db()
    try:
        yield db_pkg
    finally:
        try:
            _core.close_db()
        except Exception:
            pass


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def test_migration_adds_sessions_last_heartbeat_ts(fresh_db):
    conn = fresh_db.get_db()
    assert _column_exists(conn, "sessions", "last_heartbeat_ts")


def test_migration_creates_hook_inbox_reminders(fresh_db):
    conn = fresh_db.get_db()
    assert _table_exists(conn, "hook_inbox_reminders")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hook_inbox_reminders)").fetchall()}
    assert cols == {"sid", "last_reminder_ts"}


def test_migration_registered_in_schema_migrations(fresh_db):
    conn = fresh_db.get_db()
    versions = {
        r[0]
        for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    assert 42 in versions


def test_migration_is_idempotent(fresh_db):
    """Running init_db a second time on the same DB must not fail."""
    fresh_db.init_db()
    conn = fresh_db.get_db()
    # Still exactly one schema_migrations row for v42.
    count = conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = 42"
    ).fetchone()[0]
    assert count == 1
    # Column and table are still present and the column did not get duplicated.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
    assert cols.count("last_heartbeat_ts") == 1
    assert _table_exists(conn, "hook_inbox_reminders")
