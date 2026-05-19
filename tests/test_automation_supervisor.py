import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import automation_supervisor
from automation_supervisor import AutomationSupervisorConfig, audit_automation, format_markdown


NOW = datetime(2026, 5, 19, 6, 30, tzinfo=timezone.utc)


def _write_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "crons": [
                    {
                        "id": "email-monitor",
                        "interval_seconds": 60,
                        "run_type": "scheduled",
                        "stuck_after_seconds": 600,
                        "recovery_policy": "restart",
                        "idempotent": True,
                    },
                    {
                        "id": "followup-runner",
                        "interval_seconds": 3600,
                        "run_type": "scheduled",
                        "stuck_after_seconds": 900,
                        "recovery_policy": "catchup",
                        "idempotent": True,
                    },
                    {
                        "id": "custom-report",
                        "schedule": {"hour": 7, "minute": 0},
                        "run_type": "scheduled",
                        "stuck_after_seconds": 300,
                        "recovery_policy": "manual",
                        "idempotent": False,
                    },
                    {
                        "id": "prevent-sleep",
                        "interval_seconds": 60,
                        "run_type": "daemon",
                        "open_run_allowed": True,
                        "stuck_after_seconds": 60,
                    },
                    {
                        "id": "evolution",
                        "schedule": {"hour": 5, "minute": 0, "weekday": 0},
                        "run_type": "scheduled",
                        "stuck_after_seconds": 60,
                        "recovery_policy": "catchup",
                        "idempotent": True,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def _create_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE cron_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cron_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            exit_code INTEGER,
            summary TEXT DEFAULT '',
            error TEXT DEFAULT '',
            duration_secs REAL
        )
        """
    )
    return conn


def _config(tmp_path: Path, *, launchagent_labels: frozenset[str] | None = None) -> AutomationSupervisorConfig:
    return AutomationSupervisorConfig(
        nexo_db_path=tmp_path / "nexo.db",
        manifest_path=tmp_path / "manifest.json",
        cron_spool_dir=tmp_path / "cron-spool",
        launchagent_labels=launchagent_labels,
        now=NOW,
    )


def test_classifies_open_runs_running_stuck_retryable_and_abandoned(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    (tmp_path / "cron-spool").mkdir()
    conn = _create_db(tmp_path / "nexo.db")
    rows = [
        ("email-monitor", "2026-05-19 06:25:00", None, None),
        ("followup-runner", "2026-05-19 05:00:00", None, None),
        ("custom-report", "2026-05-19 06:00:00", None, None),
        ("legacy-orphan", "2026-05-19 04:00:00", None, None),
        ("prevent-sleep", "2026-05-18 20:00:00", None, None),
    ]
    conn.executemany("INSERT INTO cron_runs(cron_id, started_at, ended_at, exit_code) VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()

    report = audit_automation(_config(tmp_path))

    by_cron = {item["cron_id"]: item for item in report["open_runs"]}
    assert by_cron["email-monitor"]["status"] == "running"
    assert by_cron["followup-runner"]["status"] == "retryable"
    assert by_cron["custom-report"]["status"] == "stuck"
    assert by_cron["legacy-orphan"]["status"] == "abandoned"
    assert by_cron["prevent-sleep"]["status"] == "running"
    assert by_cron["prevent-sleep"]["severity"] == "OK"
    assert report["summary"]["p1"] == 3


def test_reports_launchagent_and_cron_spool_without_touching_real_agents(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    spool = tmp_path / "cron-spool"
    spool.mkdir()
    (spool / "followup-runner-1.json").write_text("{}", encoding="utf-8")
    (spool / "custom.json").write_text(json.dumps({"cron_id": "custom-report"}), encoding="utf-8")
    conn = _create_db(tmp_path / "nexo.db")
    conn.close()

    report = audit_automation(
        _config(
            tmp_path,
            launchagent_labels=frozenset(
                {
                    "com.nexo.email-monitor",
                    "com.nexo.followup-runner",
                    "com.nexo.prevent-sleep",
                }
            ),
        )
    )

    missing = {item["cron_id"] for item in report["launchagents"] if item["status"] == "missing"}
    assert missing == {"custom-report"}
    spool_by_cron = {item["cron_id"]: item for item in report["cron_spool"]}
    assert spool_by_cron["followup-runner"]["files"] == 1
    assert spool_by_cron["custom-report"]["files"] == 1
    assert all(item["status"] == "unreconciled" for item in spool_by_cron.values())
    assert any(item["kind"] == "cron_spool" and item["key"] == "followup-runner" for item in report["findings"])


def test_excludes_evolution_from_manifest_open_runs_launchagents_and_spool(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    spool = tmp_path / "cron-spool"
    spool.mkdir()
    (spool / "evolution-1.json").write_text("{}", encoding="utf-8")
    conn = _create_db(tmp_path / "nexo.db")
    conn.execute(
        "INSERT INTO cron_runs(cron_id, started_at, ended_at, exit_code) VALUES ('evolution', '2026-05-19 01:00:00', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO cron_runs(cron_id, started_at, ended_at, exit_code) VALUES ('followup-runner', '2026-05-19 05:00:00', NULL, NULL)"
    )
    conn.commit()
    conn.close()

    report = audit_automation(_config(tmp_path, launchagent_labels=frozenset({"com.nexo.evolution", "com.nexo.followup-runner"})))

    assert "evolution" in report["summary"]["excluded_jobs"]
    assert "evolution" not in {item["cron_id"] for item in report["jobs"]}
    assert "evolution" not in {item["cron_id"] for item in report["open_runs"]}
    assert "evolution" not in {item["cron_id"] for item in report["launchagents"]}
    assert "evolution" not in {item["cron_id"] for item in report["cron_spool"]}


def test_markdown_fragment_summarises_required_evidence(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    (tmp_path / "cron-spool").mkdir()
    conn = _create_db(tmp_path / "nexo.db")
    conn.execute(
        "INSERT INTO cron_runs(cron_id, started_at, ended_at, exit_code) VALUES ('custom-report', '2026-05-19 06:00:00', NULL, NULL)"
    )
    conn.commit()
    conn.close()

    md = format_markdown(audit_automation(_config(tmp_path)))

    assert "Automation supervisor" in md
    assert "Evolution excluded from cron reconciliation" in md
    assert "open_run:custom-report" in md


def test_evolution_policy_reports_loaded_for_standalone_inventory(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo-home"))
    _write_manifest(tmp_path / "manifest.json")
    (tmp_path / "cron-spool").mkdir()
    conn = _create_db(tmp_path / "nexo.db")
    conn.close()

    report = audit_automation(
        _config(tmp_path, launchagent_labels=frozenset({"com.nexo.evolution"}))
    )

    assert report["evolution"]["status"] == "enabled_and_loaded"
    assert report["evolution"]["severity"] == "OK"
    assert report["summary"]["evolution_status"] == "enabled_and_loaded"


def test_evolution_policy_requires_inventory_in_standalone_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo-home"))
    _write_manifest(tmp_path / "manifest.json")
    (tmp_path / "cron-spool").mkdir()
    conn = _create_db(tmp_path / "nexo.db")
    conn.close()

    report = audit_automation(_config(tmp_path, launchagent_labels=None))

    assert report["evolution"]["status"] == "unknown"
    assert report["evolution"]["severity"] == "P2"
    assert "inventory was not supplied" in report["evolution"]["reason"]
    assert any(item["kind"] == "evolution" for item in report["findings"])


def test_evolution_policy_reports_disabled_by_desktop_product(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "product-mode.json").write_text(json.dumps({
        "desktop_managed": True,
        "product_mode": "desktop_closed_product",
    }))
    monkeypatch.setenv("NEXO_HOME", str(home))
    _write_manifest(tmp_path / "manifest.json")
    (tmp_path / "cron-spool").mkdir()
    conn = _create_db(tmp_path / "nexo.db")
    conn.close()

    report = audit_automation(_config(tmp_path, launchagent_labels=frozenset()))

    assert report["evolution"]["status"] == "disabled_by_policy"
    assert report["evolution"]["severity"] == "OK"


def test_open_cron_rows_use_sqlite_readonly_uri(tmp_path, monkeypatch):
    db_path = tmp_path / "nexo.db"
    db_path.write_text("", encoding="utf-8")
    calls = []

    def fake_connect(database, *args, **kwargs):
        calls.append((database, kwargs))
        raise sqlite3.Error("stop after verifying URI")

    monkeypatch.setattr(automation_supervisor.sqlite3, "connect", fake_connect)

    rows = automation_supervisor._load_open_cron_rows(db_path)

    assert rows == []
    assert calls
    assert calls[0][0].startswith("file:")
    assert calls[0][0].endswith("?mode=ro")
    assert calls[0][1]["uri"] is True
