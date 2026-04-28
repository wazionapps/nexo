import importlib.util
import sqlite3
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "src" / "scripts" / "runner-health-check.py"


def _load_module(tmp_name: str = "runner_health_check_test"):
    spec = importlib.util.spec_from_file_location(tmp_name, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_check_runner_treats_sigterm_supervisor_interrupt_as_non_error(tmp_path):
    module = _load_module()
    db_path = tmp_path / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE cron_runs (
            cron_id TEXT,
            started_at TEXT,
            exit_code INTEGER,
            error TEXT DEFAULT '',
            summary TEXT DEFAULT ''
        )"""
    )
    conn.executemany(
        "INSERT INTO cron_runs (cron_id, started_at, exit_code, error, summary) VALUES (?, datetime('now', ?), ?, ?, ?)",
        [
            ("morning-agent", "-1 day", 0, "", "Briefing sent"),
            ("morning-agent", "-2 day", 0, "", "Briefing sent"),
            ("morning-agent", "-3 day", 143, "Killed by SIGTERM (exit 143)", "warnings.warn("),
            ("morning-agent", "-4 day", 143, "Killed by SIGTERM (exit 143)", "warnings.warn("),
            ("morning-agent", "-5 day", 143, "Killed by SIGTERM (exit 143)", "warnings.warn("),
            ("morning-agent", "-6 day", 1, "automation backend exited -9", "Morning agent failed"),
        ],
    )
    conn.commit()

    log_path = tmp_path / "morning-agent.log"
    log_path.write_text("recent morning-agent output\n")
    runner = {
        "cron_id": "morning-agent",
        "name": "Morning Agent",
        "stdout_log": log_path,
        "min_weekly": 3,
    }

    result = module.check_runner(conn, runner)
    conn.close()

    assert result["status"] == "PASS"
    assert result["successful_runs_last_7d"] == 5
    assert result["errors_last_7d"] == 1
    assert result["last_error"] == "automation backend exited -9"


def test_check_runner_supports_plain_sqlite_tuples(tmp_path):
    module = _load_module("runner_health_check_plain_tuple_test")
    db_path = tmp_path / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE cron_runs (
            cron_id TEXT,
            started_at TEXT,
            exit_code INTEGER,
            error TEXT DEFAULT '',
            summary TEXT DEFAULT ''
        )"""
    )
    conn.executemany(
        "INSERT INTO cron_runs (cron_id, started_at, exit_code, error, summary) VALUES (?, datetime('now', ?), ?, ?, ?)",
        [
            ("morning-agent", "-1 day", 0, "", "Briefing sent"),
            ("morning-agent", "-2 day", 143, "Killed by SIGTERM (exit 143)", "warnings.warn("),
            ("morning-agent", "-3 day", 1, "automation backend exited -9", "Morning agent failed"),
        ],
    )
    conn.commit()

    log_path = tmp_path / "morning-agent.log"
    log_path.write_text("recent morning-agent output\n")
    runner = {
        "cron_id": "morning-agent",
        "name": "Morning Agent",
        "stdout_log": log_path,
        "min_weekly": 2,
    }

    result = module.check_runner(conn, runner)
    conn.close()

    assert result["status"] == "PASS"
    assert result["successful_runs_last_7d"] == 2
    assert result["errors_last_7d"] == 1
    assert result["last_error"] == "automation backend exited -9"
