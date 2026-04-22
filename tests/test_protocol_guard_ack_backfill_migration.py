"""Regression test for runtimes stuck at schema version 48 without the
protocol guard-ack columns.

This reproduces the real-world drift where migration v22 was recorded long
before ``guard_acknowledged`` / ``guard_acknowledged_at`` were added to its
body, so subsequent ``init_db()`` calls skipped the missing columns forever.
"""
from __future__ import annotations

import importlib
import sqlite3


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def test_v49_backfills_protocol_guard_columns_for_v48_runtime(tmp_path, monkeypatch):
    test_db = str(tmp_path / "nexo.db")

    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    monkeypatch.setenv("NEXO_DB", test_db)
    monkeypatch.setenv("NEXO_TEST_DB", test_db)

    import db._core as db_core
    import db as db_pkg

    db_core.close_db()
    monkeypatch.setattr(db_core, "DB_PATH", test_db, raising=False)
    monkeypatch.setattr(db_core, "_shared_conn", None, raising=False)

    conn = sqlite3.connect(test_db)
    conn.execute(
        """
        CREATE TABLE protocol_tasks (
            task_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            goal TEXT NOT NULL,
            task_type TEXT NOT NULL DEFAULT 'answer',
            area TEXT DEFAULT '',
            project_hint TEXT DEFAULT '',
            context_hint TEXT DEFAULT '',
            files TEXT DEFAULT '[]',
            plan TEXT DEFAULT '[]',
            known_facts TEXT DEFAULT '[]',
            unknowns TEXT DEFAULT '[]',
            constraints TEXT DEFAULT '[]',
            evidence_refs TEXT DEFAULT '[]',
            verification_step TEXT DEFAULT '',
            cortex_mode TEXT DEFAULT '',
            cortex_check_id TEXT DEFAULT '',
            cortex_blocked_reason TEXT DEFAULT '',
            cortex_warnings TEXT DEFAULT '[]',
            cortex_rules TEXT DEFAULT '[]',
            opened_with_guard INTEGER NOT NULL DEFAULT 0,
            opened_with_rules INTEGER NOT NULL DEFAULT 0,
            guard_has_blocking INTEGER NOT NULL DEFAULT 0,
            guard_summary TEXT DEFAULT '',
            must_verify INTEGER NOT NULL DEFAULT 0,
            must_change_log INTEGER NOT NULL DEFAULT 0,
            must_learning_if_corrected INTEGER NOT NULL DEFAULT 1,
            must_write_diary_on_close INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            close_evidence TEXT DEFAULT '',
            files_changed TEXT DEFAULT '[]',
            correction_happened INTEGER NOT NULL DEFAULT 0,
            change_log_id INTEGER,
            learning_id INTEGER,
            followup_id TEXT DEFAULT '',
            outcome_notes TEXT DEFAULT '',
            opened_at TEXT DEFAULT (datetime('now')),
            closed_at TEXT DEFAULT NULL,
            response_mode TEXT DEFAULT '',
            response_confidence INTEGER DEFAULT 0,
            response_reasons TEXT DEFAULT '[]',
            response_high_stakes INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.executemany(
        "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
        [(version, f"migration_{version}") for version in range(1, 49)],
    )
    conn.commit()
    conn.close()

    importlib.reload(db_core)
    importlib.reload(db_pkg)
    monkeypatch.setattr(db_core, "DB_PATH", test_db, raising=False)
    monkeypatch.setattr(db_core, "_shared_conn", None, raising=False)

    db_pkg.init_db()

    conn = db_pkg.get_db()
    assert _column_exists(conn, "protocol_tasks", "guard_acknowledged")
    assert _column_exists(conn, "protocol_tasks", "guard_acknowledged_at")

    count = conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = 49"
    ).fetchone()[0]
    assert count == 1
