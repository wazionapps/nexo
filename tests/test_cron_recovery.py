"""Tests for shared cron recovery contract."""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_catchup_candidates_use_successful_cron_runs(tmp_path, monkeypatch):
    import cron_recovery

    nexo_home = tmp_path / "nexo"
    (nexo_home / "crons").mkdir(parents=True)
    (nexo_home / "data").mkdir(parents=True)
    manifest = {
        "crons": [
            {
                "id": "deep-sleep",
                "script": "scripts/nexo-deep-sleep.sh",
                "type": "shell",
                "schedule": {"hour": 4, "minute": 30},
                "recovery_policy": "catchup",
                "idempotent": True,
                "max_catchup_age": 172800,
            },
            {
                "id": "catchup",
                "script": "scripts/nexo-catchup.py",
                "interval_seconds": 900,
                "run_at_load": True,
            },
        ]
    }
    (nexo_home / "crons" / "manifest.json").write_text(json.dumps(manifest))

    db_path = nexo_home / "data" / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE cron_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, cron_id TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, exit_code INTEGER, summary TEXT, error TEXT, duration_secs REAL)"
    )
    conn.execute(
        "INSERT INTO cron_runs (cron_id, started_at, exit_code) VALUES (?, ?, ?)",
        ("deep-sleep", "2026-04-03 05:00:00", 0),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(cron_recovery, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(cron_recovery, "DB_PATH", db_path)
    monkeypatch.setattr(cron_recovery, "OPTIONALS_FILE", nexo_home / "config" / "optionals.json")
    monkeypatch.setattr(cron_recovery, "LAUNCH_AGENTS_DIR", tmp_path / "launchagents")
    monkeypatch.setattr(cron_recovery, "STATE_FILE", nexo_home / "operations" / ".catchup-state.json")
    monkeypatch.setattr(cron_recovery, "_local_timezone", lambda: timezone.utc)

    candidates = cron_recovery.catchup_candidates(now=datetime(2026, 4, 3, 6, 0, tzinfo=timezone.utc))

    assert len(candidates) == 1
    assert candidates[0]["cron_id"] == "deep-sleep"
    assert candidates[0]["missed"] is False


def test_catchup_candidates_fall_back_to_legacy_state(tmp_path, monkeypatch):
    import cron_recovery

    nexo_home = tmp_path / "nexo"
    (nexo_home / "crons").mkdir(parents=True)
    (nexo_home / "data").mkdir(parents=True)
    (nexo_home / "operations").mkdir(parents=True)
    manifest = {
        "crons": [
            {
                "id": "synthesis",
                "script": "scripts/nexo-synthesis.py",
                "schedule": {"hour": 6, "minute": 0},
                "recovery_policy": "catchup",
                "idempotent": True,
                "max_catchup_age": 172800,
            }
        ]
    }
    (nexo_home / "crons" / "manifest.json").write_text(json.dumps(manifest))
    (nexo_home / "operations" / ".catchup-state.json").write_text(json.dumps({
        "synthesis": "2026-04-03T06:10:00+00:00"
    }))

    db_path = nexo_home / "data" / "nexo.db"
    sqlite3.connect(str(db_path)).close()

    monkeypatch.setattr(cron_recovery, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(cron_recovery, "DB_PATH", db_path)
    monkeypatch.setattr(cron_recovery, "OPTIONALS_FILE", nexo_home / "config" / "optionals.json")
    monkeypatch.setattr(cron_recovery, "LAUNCH_AGENTS_DIR", tmp_path / "launchagents")
    monkeypatch.setattr(cron_recovery, "STATE_FILE", nexo_home / "operations" / ".catchup-state.json")
    monkeypatch.setattr(cron_recovery, "_local_timezone", lambda: timezone.utc)

    candidates = cron_recovery.catchup_candidates(now=datetime(2026, 4, 3, 7, 0, tzinfo=timezone.utc))

    assert len(candidates) == 1
    assert candidates[0]["missed"] is False
