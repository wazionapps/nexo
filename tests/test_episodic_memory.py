"""Tests for episodic memory diary warnings."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def test_session_diary_write_distinguishes_recent_and_historical_commit_ref_gaps(monkeypatch, tmp_path):
    from plugins import episodic_memory
    import db

    db_path = tmp_path / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE change_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_ref TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        """CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        "INSERT INTO change_log (commit_ref, created_at) VALUES ('', datetime('now', '-1 day'))"
    )
    conn.execute(
        "INSERT INTO change_log (commit_ref, created_at) VALUES ('', datetime('now', '-20 days'))"
    )
    conn.commit()

    monkeypatch.setattr(db, "delete_diary_draft", lambda sid: None)
    monkeypatch.setattr(
        episodic_memory,
        "write_session_diary",
        lambda *args, **kwargs: {"id": 1},
    )
    monkeypatch.setattr(episodic_memory, "get_db", lambda: conn)
    monkeypatch.setattr(db, "get_db", lambda: conn)

    result = episodic_memory.handle_session_diary_write(
        decisions="none",
        summary="Resumen corto",
        session_id="sid-test",
        self_critique="critica",
        domain="nexo",
    )
    conn.close()

    assert "1 changes recientes sin commit_ref (2 históricas total)" in result
