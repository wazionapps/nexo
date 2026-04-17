"""Tests for shared cron recovery contract."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


_CATCHUP_RUNTIME_FILES = (
    "cron_recovery.py",
    "runtime_power.py",
    "client_preferences.py",
    "agent_runner.py",
    "model_defaults.py",
    "model_defaults.json",
    "bootstrap_docs.py",
    "db.py",
    "enforcement_engine.py",
    "resonance_map.py",
    "constants.py",
)


def _prime_catchup_runtime_root(repo_src: Path, runtime_root: Path) -> None:
    """Copy the minimal source tree needed so nexo-catchup.py runs standalone."""
    (runtime_root / "scripts").mkdir(parents=True, exist_ok=True)
    for name in _CATCHUP_RUNTIME_FILES:
        src = repo_src / name
        if src.exists():
            shutil.copy2(src, runtime_root / name)
    shutil.copy2(repo_src / "scripts" / "nexo-catchup.py", runtime_root / "scripts" / "nexo-catchup.py")


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
    monkeypatch.setattr(cron_recovery, "load_managed_personal_crons", lambda: [])

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
    monkeypatch.setattr(cron_recovery, "load_managed_personal_crons", lambda: [])

    candidates = cron_recovery.catchup_candidates(now=datetime(2026, 4, 3, 7, 0, tzinfo=timezone.utc))

    assert len(candidates) == 1
    assert candidates[0]["missed"] is False


def test_catchup_candidates_include_managed_personal_interval(tmp_path, monkeypatch):
    import cron_recovery

    nexo_home = tmp_path / "nexo"
    (nexo_home / "crons").mkdir(parents=True)
    (nexo_home / "data").mkdir(parents=True)
    (nexo_home / "crons" / "manifest.json").write_text('{"crons":[]}')

    db_path = nexo_home / "data" / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE cron_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, cron_id TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, exit_code INTEGER, summary TEXT, error TEXT, duration_secs REAL)"
    )
    conn.execute(
        "INSERT INTO cron_runs (cron_id, started_at, exit_code) VALUES (?, ?, ?)",
        ("mail-poller", "2026-04-03 05:40:00", 0),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(cron_recovery, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(cron_recovery, "DB_PATH", db_path)
    monkeypatch.setattr(cron_recovery, "OPTIONALS_FILE", nexo_home / "config" / "optionals.json")
    monkeypatch.setattr(cron_recovery, "LAUNCH_AGENTS_DIR", tmp_path / "launchagents")
    monkeypatch.setattr(cron_recovery, "_local_timezone", lambda: timezone.utc)
    monkeypatch.setattr(cron_recovery, "load_managed_personal_crons", lambda: [{
        "id": "mail-poller",
        "script": str(nexo_home / "scripts" / "mail-poller.py"),
        "type": "python",
        "schedule_type": "interval",
        "interval_seconds": 300,
        "recovery_policy": "run_once_on_wake",
        "idempotent": True,
        "max_catchup_age": 3600,
        "run_on_boot": False,
        "run_on_wake": True,
        "personal_managed": True,
    }])

    candidates = cron_recovery.catchup_candidates(now=datetime(2026, 4, 3, 6, 0, tzinfo=timezone.utc))

    assert len(candidates) == 1
    assert candidates[0]["cron_id"] == "mail-poller"
    assert candidates[0]["missed"] is True
    assert candidates[0]["personal_managed"] is True


def test_catchup_candidates_do_not_relaunch_inflight_due_window(tmp_path, monkeypatch):
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
            }
        ]
    }
    (nexo_home / "crons" / "manifest.json").write_text(json.dumps(manifest))

    db_path = nexo_home / "data" / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE cron_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, cron_id TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, exit_code INTEGER, summary TEXT, error TEXT, duration_secs REAL)"
    )
    conn.execute(
        "INSERT INTO cron_runs (cron_id, started_at, ended_at, exit_code) VALUES (?, ?, ?, ?)",
        ("deep-sleep", "2026-04-04 04:32:49", None, None),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(cron_recovery, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(cron_recovery, "DB_PATH", db_path)
    monkeypatch.setattr(cron_recovery, "OPTIONALS_FILE", nexo_home / "config" / "optionals.json")
    monkeypatch.setattr(cron_recovery, "LAUNCH_AGENTS_DIR", tmp_path / "launchagents")
    monkeypatch.setattr(cron_recovery, "STATE_FILE", nexo_home / "operations" / ".catchup-state.json")
    monkeypatch.setattr(cron_recovery, "_local_timezone", lambda: timezone.utc)
    monkeypatch.setattr(cron_recovery, "load_managed_personal_crons", lambda: [])

    candidates = cron_recovery.catchup_candidates(now=datetime(2026, 4, 4, 4, 32, 59, tzinfo=timezone.utc))

    assert len(candidates) == 1
    assert candidates[0]["cron_id"] == "deep-sleep"
    assert candidates[0]["inflight"] is True
    assert candidates[0]["missed"] is False


def test_catchup_script_runs_directly_from_runtime_root(tmp_path):
    repo_src = Path(__file__).resolve().parent.parent / "src"
    runtime_root = tmp_path / "runtime"
    (runtime_root / "crons").mkdir(parents=True)
    _prime_catchup_runtime_root(repo_src, runtime_root)
    (runtime_root / "crons" / "manifest.json").write_text('{"crons":[]}')

    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, str(runtime_root / "scripts" / "nexo-catchup.py")],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "HOME": str(home),
            "NEXO_HOME": str(runtime_root),
            "NEXO_CODE": str(runtime_root),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_catchup_script_releases_lock_on_early_crash(tmp_path):
    """Lock must be released even if _heal_personal_schedules or catchup_candidates crashes."""
    repo_src = Path(__file__).resolve().parent.parent / "src"
    runtime_root = tmp_path / "runtime"
    (runtime_root / "crons").mkdir(parents=True)
    (runtime_root / "operations").mkdir(parents=True)
    _prime_catchup_runtime_root(repo_src, runtime_root)
    # Write a broken manifest that will cause catchup_candidates to fail
    (runtime_root / "crons" / "manifest.json").write_text('{"crons":[{"id":"boom"}]}')
    # Inject a cron_recovery that crashes in catchup_candidates
    crash_module = runtime_root / "cron_recovery.py"
    crash_module.write_text(
        "def catchup_candidates(now=None):\n"
        "    raise RuntimeError('simulated crash in catchup_candidates')\n"
    )

    home = tmp_path / "home"
    home.mkdir()
    lock_file = runtime_root / "operations" / ".catchup.lock"

    # First run — should crash but release the lock
    result = subprocess.run(
        [sys.executable, str(runtime_root / "scripts" / "nexo-catchup.py")],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "HOME": str(home),
            "NEXO_HOME": str(runtime_root),
            "NEXO_CODE": str(runtime_root),
        },
    )
    assert result.returncode != 0  # crashed

    # Restore the real module for the second run
    shutil.copy2(repo_src / "cron_recovery.py", runtime_root / "cron_recovery.py")
    (runtime_root / "crons" / "manifest.json").write_text('{"crons":[]}')

    # Second run — must NOT say "already running" (lock must have been released)
    result2 = subprocess.run(
        [sys.executable, str(runtime_root / "scripts" / "nexo-catchup.py")],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "HOME": str(home),
            "NEXO_HOME": str(runtime_root),
            "NEXO_CODE": str(runtime_root),
        },
    )
    assert result2.returncode == 0, result2.stderr
    assert "already running" not in result2.stdout


def test_catchup_script_self_heals_personal_schedules(tmp_path):
    repo_src = Path(__file__).resolve().parent.parent / "src"
    runtime_root = tmp_path / "runtime"
    marker = tmp_path / "reconcile-called.txt"
    (runtime_root / "crons").mkdir(parents=True)
    _prime_catchup_runtime_root(repo_src, runtime_root)
    (runtime_root / "crons" / "manifest.json").write_text('{"crons":[]}')
    (runtime_root / "script_registry.py").write_text(
        "from pathlib import Path\n"
        f"MARKER = Path({marker.as_posix()!r})\n"
        "def reconcile_personal_scripts(dry_run=False):\n"
        "    MARKER.write_text('called')\n"
        "    return {'ensure_schedules': {'created': [{'cron_id': 'email-monitor'}], 'repaired': [], 'invalid': []}}\n"
    )

    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, str(runtime_root / "scripts" / "nexo-catchup.py")],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "HOME": str(home),
            "NEXO_HOME": str(runtime_root),
            "NEXO_CODE": str(runtime_root),
        },
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "called"
    assert "Repaired declared personal schedules before catch-up: 1 created, 0 repaired." in result.stdout


def test_resolve_declared_schedule_spreads_weekly_machine_schedule(tmp_path, monkeypatch):
    import cron_recovery

    schedule_file = tmp_path / "schedule.json"
    schedule_file.write_text(json.dumps({
        "public_contribution": {
            "machine_id": "alpha-box",
        }
    }))

    monkeypatch.setattr(cron_recovery, "SCHEDULE_FILE", schedule_file)

    resolved = cron_recovery.resolve_declared_schedule({
        "id": "evolution",
        "schedule_strategy": "machine_weekly_spread",
        "schedule": {"hour": 5, "minute": 0, "weekday": 0},
    })

    assert resolved == {"weekday": 3, "hour": 3, "minute": 33}
