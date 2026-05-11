from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
CATCHUP_PATH = SRC_ROOT / "scripts" / "nexo-catchup.py"


def _load_catchup_module(module_name: str):
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))
    spec = importlib.util.spec_from_file_location(module_name, CATCHUP_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _create_cron_runs_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE cron_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cron_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            exit_code INTEGER,
            summary TEXT,
            error TEXT,
            duration_secs REAL
        )
        """
    )
    conn.commit()
    conn.close()


def test_catchup_direct_fallback_records_cron_runs_without_wrapper(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo"
    scripts_dir = nexo_home / "core" / "scripts"
    scripts_dir.mkdir(parents=True)
    db_path = nexo_home / "runtime" / "data" / "nexo.db"
    db_path.parent.mkdir(parents=True)
    _create_cron_runs_db(db_path)

    script = scripts_dir / "nexo-cognitive-decay.py"
    script.write_text("print('direct fallback ok')\n", encoding="utf-8")

    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("NEXO_CODE", str(SRC_ROOT))
    catchup = _load_catchup_module(f"nexo_catchup_test_{os.getpid()}_{id(tmp_path)}")
    monkeypatch.setattr(catchup, "SCRIPTS", scripts_dir)
    monkeypatch.setattr(catchup, "WRAPPER", scripts_dir / "missing-wrapper.sh")
    monkeypatch.setattr(catchup, "STATE_FILE", nexo_home / "runtime" / "operations" / ".catchup-state.json")
    monkeypatch.setattr(catchup, "LOG_FILE", nexo_home / "runtime" / "logs" / "catchup.log")
    monkeypatch.setattr(catchup, "NEXO_PYTHON", sys.executable)

    ok = catchup.run_task(
        {
            "cron_id": "cognitive-decay",
            "script": "scripts/nexo-cognitive-decay.py",
            "type": "python",
        },
        {},
    )

    assert ok is True
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT cron_id, ended_at, exit_code, summary, error FROM cron_runs"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == "cognitive-decay"
    assert row[1] is not None
    assert row[2] == 0
    assert row[3] == "direct fallback ok"
    assert row[4] == ""
