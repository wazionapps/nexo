from __future__ import annotations

import importlib
import importlib.util
import os
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
SCRIPT_PATH = REPO_SRC / "scripts" / "nexo-followup-hygiene.py"


def _load_hygiene_module():
    module_name = "nexo_followup_hygiene_test"
    sys.modules.pop(module_name, None)
    for name in ("db", "db._core", "db._schema", "db._reminders", "db._fts"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_followup_hygiene_normalizes_dirty_statuses_with_history(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    (home / "coordination").mkdir(parents=True, exist_ok=True)

    db_path = home / "data" / "nexo.db"
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    monkeypatch.setenv("NEXO_DB", str(db_path))
    monkeypatch.setenv("NEXO_TEST_DB", str(db_path))
    monkeypatch.setenv("HOME", str(home))

    import db._core as db_core
    import db._schema as db_schema
    import db

    importlib.reload(db_core)
    importlib.reload(db_schema)
    importlib.reload(db)
    db.init_db()

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, reasoning, recurrence, created_at, updated_at, priority) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("NF-DIRTY", "2026-04-01", "Dirty followup", "", "COMPLETED 2026-04-08", "", None, 1.0, 1.0, "medium"),
    )
    conn.execute(
        "INSERT INTO reminders (id, date, description, status, category, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("R-DIRTY", "2026-04-01", "Dirty reminder", "COMPLETED 2026-04-08", "general", 1.0, 1.0),
    )
    conn.commit()
    conn.close()

    module = _load_hygiene_module()
    module.main()

    conn = sqlite3.connect(str(db_path))
    followup_status = conn.execute("SELECT status FROM followups WHERE id = 'NF-DIRTY'").fetchone()[0]
    reminder_status = conn.execute("SELECT status FROM reminders WHERE id = 'R-DIRTY'").fetchone()[0]
    followup_history = conn.execute(
        """SELECT event_type, note, actor
           FROM item_history
           WHERE item_type = 'followup' AND item_id = 'NF-DIRTY'
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    reminder_history = conn.execute(
        """SELECT event_type, note, actor
           FROM item_history
           WHERE item_type = 'reminder' AND item_id = 'R-DIRTY'
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    conn.close()

    assert followup_status == "COMPLETED"
    assert reminder_status == "COMPLETED"
    assert followup_history == (
        "normalized",
        "Weekly hygiene normalized dirty status from COMPLETED 2026-04-08 to COMPLETED.",
        "followup-hygiene",
    )
    assert reminder_history == (
        "normalized",
        "Weekly hygiene normalized dirty status from COMPLETED 2026-04-08 to COMPLETED.",
        "followup-hygiene",
    )
