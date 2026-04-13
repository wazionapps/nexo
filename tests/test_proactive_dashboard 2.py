from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
SCRIPT_PATH = REPO_SRC / "scripts" / "nexo-proactive-dashboard.py"


def _load_module(module_name: str = "nexo_proactive_dashboard_test"):
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_proactive_dashboard_ignores_deleted_waiting_and_cancelled_items(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    data_dir = home / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "nexo.db"

    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    monkeypatch.setenv("NEXO_DB", str(db_path))

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE followups (id TEXT, description TEXT, date TEXT, created_at REAL, reasoning TEXT, status TEXT)"
    )
    conn.execute(
        "CREATE TABLE reminders (id TEXT, description TEXT, date TEXT, created_at REAL, status TEXT)"
    )
    conn.executemany(
        "INSERT INTO followups VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("NF-OPEN", "Open followup", "2026-04-01", 1.0, "", "PENDING"),
            ("NF-DELETED", "Deleted followup", "2026-04-01", 1.0, "", "DELETED"),
            ("NF-WAITING", "Waiting followup", "2026-04-01", 1.0, "", "waiting"),
        ],
    )
    conn.executemany(
        "INSERT INTO reminders VALUES (?, ?, ?, ?, ?)",
        [
            ("R-OPEN", "Open reminder", "2026-04-01", 1.0, "PENDING"),
            ("R-CANCELLED", "Cancelled reminder", "2026-04-01", 1.0, "CANCELLED"),
            ("R-DELETED", "Deleted reminder", "", 1.0, "DELETED"),
        ],
    )
    conn.commit()
    conn.close()

    module = _load_module()
    overdue_followups = module.check_overdue_followups()
    overdue_reminders = module.check_overdue_reminders()
    stale_ideas = module.check_stale_ideas()

    assert [item["id"] for item in overdue_followups] == ["NF-OPEN"]
    assert [item["id"] for item in overdue_reminders] == ["R-OPEN"]
    assert stale_ideas == []
