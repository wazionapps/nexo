from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "src" / "scripts" / "nexo-cron-wrapper.sh"


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


def test_cron_wrapper_writes_completed_row(tmp_path):
    nexo_home = tmp_path / "nexo"
    db_dir = nexo_home / "data"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "nexo.db"
    _create_cron_runs_db(db_path)

    result = subprocess.run(
        ["bash", str(WRAPPER), "impact-scorer", "bash", "-lc", "echo cron-ok"],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "NEXO_HOME": str(nexo_home)},
    )

    assert result.returncode == 0, result.stderr

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT cron_id, ended_at, exit_code, summary, error, duration_secs FROM cron_runs"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "impact-scorer"
    assert row[1] is not None
    assert row[2] == 0
    assert row[3] == "cron-ok"
    assert row[4] == ""
    assert row[5] is not None


def test_cron_wrapper_spools_when_db_write_fails(tmp_path):
    nexo_home = tmp_path / "nexo"
    (nexo_home / "data").mkdir(parents=True)

    result = subprocess.run(
        ["bash", str(WRAPPER), "impact-scorer", "bash", "-lc", "echo spool-ok"],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "NEXO_HOME": str(nexo_home)},
    )

    assert result.returncode == 0, result.stderr

    spool_dir = nexo_home / "operations" / "cron-spool"
    spool_files = sorted(spool_dir.glob("impact-scorer-*.json"))
    assert len(spool_files) == 1

    payload = json.loads(spool_files[0].read_text(encoding="utf-8"))
    assert payload["cron_id"] == "impact-scorer"
    assert payload["exit_code"] == 0
    assert payload["summary"] == "spool-ok"
    assert payload["ended_at"]
