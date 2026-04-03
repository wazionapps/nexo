"""Tests for the Doctor diagnostic system."""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture
def nexo_home(tmp_path, monkeypatch):
    """Create a minimal NEXO_HOME for doctor tests."""
    home = tmp_path / "nexo"
    for d in ["data", "scripts", "plugins", "crons", "hooks", "coordination", "operations", "logs"]:
        (home / d).mkdir(parents=True)

    # Create a minimal DB
    import sqlite3
    db_path = home / "data" / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT, status TEXT, started_at TEXT)")
    conn.execute("PRAGMA user_version = 42")
    conn.close()

    monkeypatch.setenv("NEXO_HOME", str(home))

    # Patch module-level NEXO_HOME in all doctor modules
    from doctor.providers import boot, runtime, deep
    monkeypatch.setattr(boot, "NEXO_HOME", home)
    monkeypatch.setattr(runtime, "NEXO_HOME", home)
    monkeypatch.setattr(deep, "NEXO_HOME", home)

    return home


class TestBootChecks:
    def test_healthy_system(self, nexo_home):
        from doctor.providers.boot import run_boot_checks
        checks = run_boot_checks()
        statuses = [c.status for c in checks]
        assert "critical" not in statuses

    def test_missing_db(self, nexo_home):
        os.remove(str(nexo_home / "data" / "nexo.db"))
        from doctor.providers.boot import check_db_exists
        check = check_db_exists()
        assert check.status == "critical"

    def test_missing_dirs_fix(self, nexo_home):
        import shutil
        shutil.rmtree(str(nexo_home / "coordination"))
        from doctor.providers.boot import run_boot_checks
        checks = run_boot_checks(fix=True)
        dir_check = [c for c in checks if c.id == "boot.required_dirs"][0]
        assert dir_check.fixed
        assert (nexo_home / "coordination").is_dir()


class TestRuntimeChecks:
    def test_fresh_immune(self, nexo_home):
        status = {"overall_status": "healthy", "checks": [1, 2, 3]}
        (nexo_home / "coordination" / "immune-status.json").write_text(json.dumps(status))
        from doctor.providers.runtime import check_immune_status
        check = check_immune_status()
        assert check.status == "healthy"

    def test_stale_watchdog(self, nexo_home):
        status_file = nexo_home / "operations" / "watchdog-status.json"
        status_file.write_text('{"monitors_total": 5, "monitors_pass": 5, "monitors_fail": 0}')
        # Make it old
        old_time = time.time() - 7200
        os.utime(str(status_file), (old_time, old_time))
        from doctor.providers.runtime import check_watchdog_status
        check = check_watchdog_status()
        assert check.status == "degraded"

    def test_missing_watchdog(self, nexo_home):
        from doctor.providers.runtime import check_watchdog_status
        check = check_watchdog_status()
        assert check.status == "degraded"


class TestDeepChecks:
    def test_schema_version(self, nexo_home):
        from doctor.providers.deep import check_schema_version
        check = check_schema_version()
        assert check.status == "healthy"
        assert "42" in check.summary

    def test_missing_self_audit(self, nexo_home):
        from doctor.providers.deep import check_self_audit_summary
        check = check_self_audit_summary()
        assert check.status == "degraded"


class TestOrchestrator:
    def test_boot_tier(self, nexo_home):
        from doctor.orchestrator import run_doctor
        report = run_doctor(tier="boot")
        assert report.overall_status in ("healthy", "degraded", "critical")
        assert report.duration_ms >= 0
        assert len(report.checks) > 0

    def test_all_tiers(self, nexo_home):
        from doctor.orchestrator import run_doctor
        report = run_doctor(tier="all")
        tiers = {c.tier for c in report.checks}
        assert "boot" in tiers
        assert "runtime" in tiers
        assert "deep" in tiers


class TestFormatters:
    def test_json_format(self, nexo_home):
        from doctor.orchestrator import run_doctor
        from doctor.formatters import format_report
        report = run_doctor(tier="boot")
        output = format_report(report, fmt="json")
        data = json.loads(output)
        assert "overall_status" in data
        assert "checks" in data

    def test_text_format(self, nexo_home):
        from doctor.orchestrator import run_doctor
        from doctor.formatters import format_report
        report = run_doctor(tier="boot")
        output = format_report(report, fmt="text")
        assert "NEXO Doctor" in output
        assert "BOOT" in output
