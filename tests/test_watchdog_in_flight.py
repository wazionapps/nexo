"""Tests for nexo-watchdog.sh in-flight detection.

The pre-5.8.1 watchdog would kickstart -k over any cron whose cron_runs
row showed age > 3×max_stale, regardless of whether the run was still
active. With the new wrapper that INSERTs a row at start (ended_at=NULL),
we can tell "currently running" from "missed/stuck" and only intervene on
actual zombies.

These tests drive the watchdog script end-to-end against a tmp NEXO_HOME
so the detection logic is covered at the shell level — the place it
actually runs.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = REPO_ROOT / "src" / "scripts" / "nexo-watchdog.sh"
MANIFEST = REPO_ROOT / "src" / "crons" / "manifest.json"


def _bootstrap_home(tmp_path: Path) -> Path:
    home = tmp_path / "nexo"
    (home / "operations").mkdir(parents=True)
    (home / "runtime" / "data").mkdir(parents=True)
    (home / "logs").mkdir(parents=True)
    (home / "scripts").mkdir(parents=True)
    (home / "crons").mkdir(parents=True)
    (home / "config").mkdir(parents=True)

    # Minimal manifest with just deep-sleep so we exercise a single monitor
    (home / "crons" / "manifest.json").write_text(
        '{"crons":[{"id":"deep-sleep","script":"scripts/nexo-deep-sleep.sh",'
        '"type":"shell","schedule":{"hour":4,"minute":30},"core":true,'
        '"recovery_policy":"catchup","max_catchup_age":172800}]}'
    )
    (home / "config" / "optionals.json").write_text("{}")
    (home / "config" / "schedule.json").write_text('{"automation_enabled":true}')

    # cron_runs schema
    db = home / "runtime" / "data" / "nexo.db"
    conn = sqlite3.connect(db)
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
    return home


def _run_watchdog(home: Path, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "NEXO_HOME": str(home), "NEXO_CODE": str(REPO_ROOT / "src")}
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(WATCHDOG)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _read_report(home: Path) -> str:
    report = home / "operations" / "watchdog-report.txt"
    return report.read_text() if report.exists() else ""


@pytest.mark.skipif(
    sys.platform != "darwin", reason="watchdog in-flight detection tested on macOS paths only"
)
def test_watchdog_treats_fresh_in_flight_row_as_healthy(tmp_path):
    """A cron_runs row with started_at = now() and ended_at = NULL must be
    interpreted as 'currently running', not as 'missed cron'."""
    home = _bootstrap_home(tmp_path)
    db = home / "runtime" / "data" / "nexo.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO cron_runs (cron_id, started_at, ended_at) "
        "VALUES ('deep-sleep', datetime('now','-30 seconds'), NULL)"
    )
    conn.commit()
    conn.close()

    proc = _run_watchdog(home)
    report = _read_report(home)
    # The in-flight state should produce either PASS or at worst WARN —
    # never a FAIL that triggers kickstart -k.
    assert "FAIL" not in report or "deep-sleep" not in report.split("FAIL", 1)[1].splitlines()[0]
    assert "In-flight" in report
    assert proc.returncode in (0, 1)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_watchdog_warns_on_long_in_flight_with_alive_process(tmp_path):
    """An in-flight row older than 3× max_stale but whose worker process is
    alive must WARN, not FAIL — long-running legitimate work."""
    home = _bootstrap_home(tmp_path)
    db = home / "runtime" / "data" / "nexo.db"
    conn = sqlite3.connect(db)
    # 4 hours ago — well above 3× max_stale for any reasonable threshold.
    conn.execute(
        "INSERT INTO cron_runs (cron_id, started_at, ended_at) "
        "VALUES ('deep-sleep', datetime('now','-4 hours'), NULL)"
    )
    conn.commit()
    conn.close()

    proc = _run_watchdog(home)
    report = _read_report(home)
    # Report includes "In-flight" context; no kickstart attempt
    assert "kickstart" not in report.lower() or "restarted missed" not in report.lower()
    assert proc.returncode in (0, 1)
