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
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE change_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            files TEXT DEFAULT '',
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
        "INSERT INTO change_log (files, commit_ref, created_at) VALUES ('src/plugins/protocol.py', '', datetime('now', '-1 day'))"
    )
    conn.execute(
        "INSERT INTO change_log (files, commit_ref, created_at) VALUES ('src/plugins/protocol.py', '', datetime('now', '-20 days'))"
    )
    conn.execute(
        "INSERT INTO change_log (files, commit_ref, created_at) VALUES ('/home/user/.nexo/operations/orchestrator-state.json', '', datetime('now', '-1 day'))"
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

    assert "1 cambio reciente de repo sin commit_ref (2 cambios de repo total)" in result


def test_change_log_message_distinguishes_repo_and_local_commit_refs(monkeypatch):
    from plugins import episodic_memory

    captured = []

    def _fake_log_change(session_id, files, what_changed, why, triggered_by, affects, risks, verify, commit_ref):
        captured.append((session_id, files, commit_ref))
        return {"id": len(captured)}

    monkeypatch.setattr(episodic_memory, "log_change", _fake_log_change)
    monkeypatch.setattr(episodic_memory, "_cognitive_ingest_safe", lambda *args, **kwargs: None)

    repo_msg = episodic_memory.handle_change_log(
        files="src/plugins/protocol.py",
        what_changed="Ajuste de validación",
        why="Corregir warning engañoso",
        session_id="sid-test",
    )
    local_msg = episodic_memory.handle_change_log(
        files="/home/user/.nexo/operations/orchestrator-state.json",
        what_changed="Checkpoint local",
        why="Persistir continuidad",
        session_id="sid-test",
    )
    benchmark_msg = episodic_memory.handle_change_log(
        files="benchmarks/README.md",
        what_changed="Actualizar benchmark",
        why="Documentar comparativa",
        session_id="sid-test",
    )

    assert "nexo_change_commit(1, 'hash')" in repo_msg
    assert "'server-direct'" in local_msg
    assert "'local-uncommitted'" in local_msg
    assert "nexo_change_commit(3, 'hash')" in benchmark_msg
