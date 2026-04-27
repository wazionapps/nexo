"""Tests for nexo-watchdog.sh STUCK CRON REAPER (v7.11.2).

The v5.8.1 in-flight detection prevented the watchdog from kickstart -k'ing
running jobs (deep-sleep was being killed mid-flight 2026-04-14..17). The
fix made the watchdog leave any row with started_at present and ended_at
NULL alone.

Mirror-image gap closed by the reaper: rows that stay in-flight forever
because the wrapper child is genuinely hung (e.g. headless `claude --bare`
blocked on an MCP marked restart_required) blocked the next tick with
"Another instance running. Skipping". Morning brief, followup runner and
orchestrator-v2 went silent for days (2026-04-24..27).

The reaper closes that gap with per-cron `stuck_after_seconds` thresholds
in manifest.json. Defaults are generous (12h global; deep-sleep 8h; sleep
and evolution 4h) so the v5.8.1 bug cannot recur.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = REPO_ROOT / "src" / "scripts" / "nexo-watchdog.sh"
WRAPPER = REPO_ROOT / "src" / "scripts" / "nexo-cron-wrapper.sh"


def _bootstrap_home(tmp_path: Path, manifest_overrides: dict | None = None) -> Path:
    home = tmp_path / "nexo"
    (home / "runtime" / "operations").mkdir(parents=True)
    (home / "runtime" / "data").mkdir(parents=True)
    (home / "runtime" / "logs").mkdir(parents=True)
    (home / "runtime" / "crons").mkdir(parents=True)
    (home / "runtime" / "backups").mkdir(parents=True)
    (home / "personal" / "config").mkdir(parents=True)
    (home / "core" / "scripts").mkdir(parents=True)

    base_cron = {
        "id": "deep-sleep",
        "script": "scripts/nexo-deep-sleep.sh",
        "type": "shell",
        "schedule": {"hour": 4, "minute": 30},
        "core": True,
        "recovery_policy": "catchup",
        "max_catchup_age": 172800,
    }
    if manifest_overrides:
        base_cron.update(manifest_overrides)
    manifest = {"crons": [base_cron]}
    (home / "runtime" / "crons" / "manifest.json").write_text(json.dumps(manifest))
    (home / "personal" / "config" / "optionals.json").write_text("{}")
    (home / "personal" / "config" / "schedule.json").write_text(
        '{"automation_enabled":true}'
    )

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
        timeout=60,
        env=env,
    )


def _read_row(db: Path, row_id: int):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT ended_at, exit_code, summary FROM cron_runs WHERE id=?",
            (row_id,),
        ).fetchone()
    finally:
        conn.close()


def _read_status_json(home: Path) -> dict:
    p = home / "runtime" / "operations" / "watchdog-status.json"
    return json.loads(p.read_text()) if p.exists() else {}


@pytest.mark.skipif(
    sys.platform != "darwin", reason="reaper tested on macOS paths only"
)
def test_reaper_leaves_fresh_in_flight_alone(tmp_path):
    """A row started 30s ago must NOT be reaped — guards against the v5.8.1
    regression of killing running jobs."""
    home = _bootstrap_home(tmp_path)
    db = home / "runtime" / "data" / "nexo.db"
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO cron_runs (cron_id, started_at, ended_at) "
        "VALUES ('deep-sleep', datetime('now','-30 seconds'), NULL)"
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()

    _run_watchdog(home)
    status = _read_status_json(home)
    assert status.get("summary", {}).get("reaped", 0) == 0
    ended_at, exit_code, _ = _read_row(db, row_id)
    assert ended_at is None
    assert exit_code is None


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_reaper_respects_per_cron_threshold(tmp_path):
    """deep-sleep with `stuck_after_seconds: 28800` (8h) must NOT be reaped
    at 4h. Direct guard against re-introducing the deep-sleep kill loop."""
    home = _bootstrap_home(tmp_path, {"stuck_after_seconds": 28800})
    db = home / "runtime" / "data" / "nexo.db"
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO cron_runs (cron_id, started_at, ended_at) "
        "VALUES ('deep-sleep', datetime('now','-4 hours'), NULL)"
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()

    _run_watchdog(home)
    status = _read_status_json(home)
    assert status.get("summary", {}).get("reaped", 0) == 0
    ended_at, _, _ = _read_row(db, row_id)
    assert ended_at is None


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_reaper_cleans_orphan_zombie_row(tmp_path):
    """An in-flight row >12h old (default threshold) WITHOUT a live wrapper
    PID must be cleaned in-band: ended_at set, exit_code=137. Otherwise the
    next tick keeps skipping with 'Another instance running'."""
    home = _bootstrap_home(tmp_path)
    db = home / "runtime" / "data" / "nexo.db"
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO cron_runs (cron_id, started_at, ended_at) "
        "VALUES ('orchestrator-v2', datetime('now','-25 hours'), NULL)"
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()

    _run_watchdog(home)
    ended_at, exit_code, summary = _read_row(db, row_id)
    assert ended_at is not None, "orphan row must be closed"
    assert exit_code == 137
    assert "stuck row reaped" in (summary or "")
    status = _read_status_json(home)
    assert status.get("summary", {}).get("reaped", 0) >= 1


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_reaper_kills_live_wrapper_for_stuck_cron(tmp_path):
    """A real wrapper running `sleep 99999` whose cron_runs row is backdated
    past the threshold must be SIGTERM'd. The wrapper trap then closes the
    row with exit 143."""
    home = _bootstrap_home(tmp_path)
    db = home / "runtime" / "data" / "nexo.db"

    cron_id = f"test-reaper-victim-{os.getpid()}"
    env = {**os.environ, "NEXO_HOME": str(home)}
    child = subprocess.Popen(
        ["bash", str(WRAPPER), cron_id, "sleep", "99999"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 5
        row_id = None
        while time.time() < deadline:
            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT id FROM cron_runs WHERE cron_id=? ORDER BY id DESC LIMIT 1",
                (cron_id,),
            ).fetchone()
            conn.close()
            if row:
                row_id = row[0]
                break
            time.sleep(0.1)
        assert row_id is not None, "wrapper failed to insert cron_runs row"

        conn = sqlite3.connect(db)
        conn.execute(
            "UPDATE cron_runs SET started_at=datetime('now','-25 hours') WHERE id=?",
            (row_id,),
        )
        conn.commit()
        conn.close()

        _run_watchdog(home)

        ended_at, exit_code, _ = _read_row(db, row_id)
        assert ended_at is not None, "row must be closed after reap"
        assert exit_code == 143, "wrapper trap should record SIGTERM exit"

        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pytest.fail("wrapper PID still alive after reaper")
    finally:
        if child.poll() is None:
            child.kill()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_reaper_skips_self(tmp_path):
    """cron_id='watchdog' must never be reaped — would be self-immolation
    while the watchdog tick is mid-execution."""
    home = _bootstrap_home(tmp_path)
    db = home / "runtime" / "data" / "nexo.db"
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO cron_runs (cron_id, started_at, ended_at) "
        "VALUES ('watchdog', datetime('now','-25 hours'), NULL)"
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()

    _run_watchdog(home)
    ended_at, _, _ = _read_row(db, row_id)
    assert ended_at is None, "watchdog must not reap itself"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_reaper_uses_default_threshold_for_unlisted_cron(tmp_path):
    """A cron not in manifest.json should fall back to the global default
    (12h). A row 6h old must NOT be reaped under that default."""
    home = _bootstrap_home(tmp_path)
    db = home / "runtime" / "data" / "nexo.db"
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO cron_runs (cron_id, started_at, ended_at) "
        "VALUES ('unlisted-personal-script', datetime('now','-6 hours'), NULL)"
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()

    _run_watchdog(home)
    ended_at, _, _ = _read_row(db, row_id)
    assert ended_at is None, "6h-old unlisted cron must not be reaped at default threshold"
