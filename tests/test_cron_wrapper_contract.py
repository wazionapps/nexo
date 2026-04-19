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
    db_dir = nexo_home / "runtime" / "data"
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

    spool_dir = nexo_home / "runtime" / "operations" / "cron-spool"
    spool_files = sorted(spool_dir.glob("impact-scorer-*.json"))
    assert len(spool_files) == 1

    payload = json.loads(spool_files[0].read_text(encoding="utf-8"))
    assert payload["cron_id"] == "impact-scorer"
    assert payload["exit_code"] == 0
    assert payload["summary"] == "spool-ok"
    assert payload["ended_at"]


def test_cron_wrapper_inserts_in_flight_row_at_start(tmp_path):
    """The wrapper must INSERT an in-flight row (ended_at=NULL) before the
    child starts. Without this, any wrapper that dies before UPDATE leaves
    no record, and the watchdog cannot distinguish `missing` from `running`.
    The old wrapper only wrote on exit — that's what wedged deep-sleep
    between 2026-04-14 and 2026-04-17.
    """
    import time

    nexo_home = tmp_path / "nexo"
    db_dir = nexo_home / "runtime" / "data"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "nexo.db"
    _create_cron_runs_db(db_path)

    proc = subprocess.Popen(
        ["bash", str(WRAPPER), "deep-sleep", "bash", "-lc", "sleep 2; echo done"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "NEXO_HOME": str(nexo_home)},
    )
    try:
        time.sleep(0.8)
        conn = sqlite3.connect(db_path)
        try:
            in_flight = conn.execute(
                "SELECT cron_id, started_at, ended_at, exit_code FROM cron_runs"
            ).fetchone()
        finally:
            conn.close()
        assert in_flight is not None
        assert in_flight[0] == "deep-sleep"
        assert in_flight[1]
        assert in_flight[2] is None
        assert in_flight[3] is None
    finally:
        proc.wait(timeout=10)

    assert proc.returncode == 0

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT cron_id, ended_at, exit_code, summary FROM cron_runs"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "deep-sleep"
    assert rows[0][1] is not None
    assert rows[0][2] == 0
    assert rows[0][3] == "done"


def test_cron_wrapper_closes_row_on_sigterm(tmp_path):
    """SIGTERM to the wrapper must still close the cron_runs row with
    exit_code=143 + 'Killed by SIGTERM' so the watchdog stops treating the
    cron as 'missing a final record' and looping kickstart -k over it.
    """
    import signal
    import time

    nexo_home = tmp_path / "nexo"
    db_dir = nexo_home / "runtime" / "data"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "nexo.db"
    _create_cron_runs_db(db_path)

    proc = subprocess.Popen(
        ["bash", str(WRAPPER), "deep-sleep", "bash", "-lc", "sleep 30"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "NEXO_HOME": str(nexo_home)},
    )
    try:
        time.sleep(0.8)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()

    assert proc.returncode == 143

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT cron_id, ended_at, exit_code, error FROM cron_runs"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "deep-sleep"
    assert row[1] is not None
    assert row[2] == 143
    assert "SIGTERM" in (row[3] or "")
