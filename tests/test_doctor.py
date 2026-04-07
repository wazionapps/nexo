"""Tests for the Doctor diagnostic system."""
import datetime
import json
import os
import plistlib
import sqlite3
import textwrap
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture
def nexo_home(tmp_path, monkeypatch):
    """Create a minimal NEXO_HOME for doctor tests."""
    home = tmp_path / "nexo"
    for d in ["data", "scripts", "plugins", "crons", "hooks", "coordination", "operations", "logs"]:
        (home / d).mkdir(parents=True)

    (home / "crons" / "manifest.json").write_text(json.dumps({
        "crons": [
            {"id": "immune", "interval_seconds": 1800},
            {"id": "watchdog", "interval_seconds": 1800},
            {"id": "self-audit", "schedule": {"hour": 7, "minute": 0}},
        ]
    }))

    # Create a minimal DB with the real columns Doctor inspects.
    db_path = home / "data" / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "sid TEXT PRIMARY KEY, task TEXT NOT NULL DEFAULT '', started_epoch REAL NOT NULL, "
        "last_update_epoch REAL NOT NULL, local_time TEXT NOT NULL DEFAULT ''"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cron_runs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, cron_id TEXT NOT NULL, started_at TEXT NOT NULL"
        ")"
    )
    conn.execute("PRAGMA user_version = 42")
    conn.close()

    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    # Force doctor imports to reload from this repo, not from any operator-local runtime.
    repo_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    if repo_src in sys.path:
        sys.path.remove(repo_src)
    sys.path.insert(0, repo_src)
    for name in list(sys.modules):
        if name == "doctor" or name.startswith("doctor."):
            sys.modules.pop(name, None)

    # Patch module-level NEXO_HOME in all doctor modules
    from doctor.providers import boot, runtime, deep
    monkeypatch.setattr(boot, "NEXO_HOME", home)
    monkeypatch.setattr(runtime, "NEXO_HOME", home)
    monkeypatch.setattr(deep, "NEXO_HOME", home)

    return home


def _create_protocol_tables(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS protocol_tasks (
            task_id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'open',
            must_verify INTEGER DEFAULT 0,
            close_evidence TEXT DEFAULT '',
            must_change_log INTEGER DEFAULT 0,
            change_log_id INTEGER,
            correction_happened INTEGER DEFAULT 0,
            learning_id INTEGER,
            task_type TEXT DEFAULT 'answer',
            cortex_mode TEXT DEFAULT '',
            opened_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS protocol_debt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            debt_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'warn',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.commit()
    conn.close()


def _create_learnings_table(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS learnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            applies_to TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.commit()
    conn.close()


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

    def test_config_parse_catches_broken_manifest(self, nexo_home):
        (nexo_home / "crons" / "manifest.json").write_text("{not valid json")
        from doctor.providers.boot import check_config_parse
        check = check_config_parse()
        assert check.status == "degraded"
        assert any("crons/manifest.json" in ev for ev in check.evidence)

    def test_config_parse_catches_broken_optionals(self, nexo_home):
        (nexo_home / "config").mkdir(parents=True, exist_ok=True)
        (nexo_home / "config" / "optionals.json").write_text("[]")
        from doctor.providers.boot import check_config_parse
        check = check_config_parse()
        assert check.status == "degraded"
        assert any("optionals.json" in ev for ev in check.evidence)

    def test_config_parse_healthy_when_all_valid(self, nexo_home):
        (nexo_home / "config").mkdir(parents=True, exist_ok=True)
        (nexo_home / "config" / "schedule.json").write_text('{"automation_enabled": true}')
        (nexo_home / "config" / "optionals.json").write_text('{"automation": true}')
        from doctor.providers.boot import check_config_parse
        check = check_config_parse()
        assert check.status == "healthy"
        assert set(check.evidence) == {"schedule.json", "optionals.json", "crons/manifest.json"}


class TestRuntimeChecks:
    def test_fresh_immune(self, nexo_home):
        status = {
            "counts": {"OK": 3, "WARN": 0, "FAIL": 0},
            "checks": {"db": [1], "services": [1, 2]},
        }
        (nexo_home / "coordination" / "immune-status.json").write_text(json.dumps(status))
        from doctor.providers.runtime import check_immune_status
        check = check_immune_status()
        assert check.status == "healthy"

    def test_stale_watchdog(self, nexo_home):
        status_file = nexo_home / "operations" / "watchdog-status.json"
        status_file.write_text(json.dumps({
            "summary": {"total": 5, "pass": 5, "warn": 0, "fail": 0, "overall": "PASS"}
        }))
        # Make it old
        old_time = time.time() - 7200
        os.utime(str(status_file), (old_time, old_time))
        from doctor.providers.runtime import check_watchdog_status
        check = check_watchdog_status()
        assert check.status == "degraded"

    def test_watchdog_fail_is_critical(self, nexo_home):
        status_file = nexo_home / "operations" / "watchdog-status.json"
        status_file.write_text(json.dumps({
            "summary": {"total": 5, "pass": 3, "warn": 1, "fail": 1, "overall": "FAIL"}
        }))
        from doctor.providers.runtime import check_watchdog_status
        check = check_watchdog_status()
        assert check.status == "critical"

    def test_missing_watchdog(self, nexo_home):
        from doctor.providers.runtime import check_watchdog_status
        check = check_watchdog_status()
        assert check.status == "degraded"

    def test_state_watchers_summary_surfaces_critical(self, nexo_home):
        conn = sqlite3.connect(str(nexo_home / "data" / "nexo.db"))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS state_watchers (
                watcher_id TEXT PRIMARY KEY,
                watcher_type TEXT NOT NULL,
                title TEXT NOT NULL,
                target TEXT DEFAULT '',
                severity TEXT NOT NULL DEFAULT 'warn',
                status TEXT NOT NULL DEFAULT 'active',
                config TEXT DEFAULT '{}',
                last_health TEXT NOT NULL DEFAULT 'unknown',
                last_result TEXT DEFAULT '{}',
                last_checked_at TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            "INSERT INTO state_watchers (watcher_id, watcher_type, title, status) VALUES (?, ?, ?, 'active')",
            ("SW-1", "expiry", "SSL cert"),
        )
        conn.commit()
        conn.close()

        (nexo_home / "operations" / "state-watchers-status.json").write_text(json.dumps({
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "watcher_count": 1,
            "counts": {"healthy": 0, "degraded": 0, "critical": 1, "unknown": 0},
            "watchers": [{"watcher_id": "SW-1", "health": "critical"}],
        }))

        from doctor.providers.runtime import check_state_watchers
        check = check_state_watchers()
        assert check.status == "critical"

    def test_stale_sessions_uses_last_update_epoch(self, nexo_home):
        conn = sqlite3.connect(str(nexo_home / "data" / "nexo.db"))
        conn.execute(
            "INSERT INTO sessions (sid, task, started_epoch, last_update_epoch, local_time) VALUES (?, ?, ?, ?, ?)",
            ("nexo-123-456", "audit", time.time() - 10800, time.time() - 10800, "10:00"),
        )
        conn.commit()
        conn.close()

        from doctor.providers.runtime import check_stale_sessions
        check = check_stale_sessions()
        assert check.status == "degraded"

    def test_cron_freshness_respects_daily_schedule(self, nexo_home):
        conn = sqlite3.connect(str(nexo_home / "data" / "nexo.db"))
        conn.execute(
            "INSERT INTO cron_runs (cron_id, started_at) VALUES (?, ?)",
            ("self-audit", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 12 * 3600))),
        )
        conn.commit()
        conn.close()

        from doctor.providers.runtime import check_cron_freshness
        check = check_cron_freshness()
        assert check.status == "healthy"

    def test_cron_freshness_flags_stale_interval_cron(self, nexo_home):
        conn = sqlite3.connect(str(nexo_home / "data" / "nexo.db"))
        conn.execute(
            "INSERT INTO cron_runs (cron_id, started_at) VALUES (?, ?)",
            ("watchdog", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 3 * 3600))),
        )
        conn.commit()
        conn.close()

        from doctor.providers.runtime import check_cron_freshness
        check = check_cron_freshness()
        assert check.status == "degraded"

    def test_cron_freshness_ignores_run_at_load_jobs(self, nexo_home):
        (nexo_home / "crons" / "manifest.json").write_text(json.dumps({
            "crons": [
                {"id": "catchup", "run_at_load": True},
            ]
        }))

        conn = sqlite3.connect(str(nexo_home / "data" / "nexo.db"))
        conn.execute(
            "INSERT INTO cron_runs (cron_id, started_at) VALUES (?, ?)",
            ("catchup", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 3 * 3600))),
        )
        conn.commit()
        conn.close()

        from doctor.providers.runtime import check_cron_freshness
        check = check_cron_freshness()
        assert check.status == "healthy"

    def test_cron_freshness_tracks_interval_jobs_even_if_run_at_load(self, nexo_home):
        (nexo_home / "crons" / "manifest.json").write_text(json.dumps({
            "crons": [
                {"id": "catchup", "interval_seconds": 900, "run_at_load": True},
            ]
        }))

        conn = sqlite3.connect(str(nexo_home / "data" / "nexo.db"))
        conn.execute(
            "INSERT INTO cron_runs (cron_id, started_at) VALUES (?, ?)",
            ("catchup", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 3 * 3600))),
        )
        conn.commit()
        conn.close()

        from doctor.providers.runtime import check_cron_freshness
        check = check_cron_freshness()
        assert check.status == "degraded"

    def test_launchagent_integrity_detects_tmp_drift(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        plist_path = nexo_home / "com.nexo.deep-sleep.plist"
        with plist_path.open("wb") as fh:
            plistlib.dump({
                "EnvironmentVariables": {
                    "NEXO_HOME": str(nexo_home),
                    "NEXO_CODE": str(nexo_home / "src"),
                },
                "StartInterval": 1800,
            }, fh)

        monkeypatch.setattr(runtime.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(runtime, "_managed_launchagent_plists", lambda: [("deep-sleep", plist_path)])
        monkeypatch.setattr(runtime.os, "getuid", lambda: 501)

        def fake_run(args, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "gui/501/com.nexo.deep-sleep = {\n"
                    "path = /private/tmp/nexo-audit-install-XYZ/Library/LaunchAgents/com.nexo.deep-sleep.plist\n"
                    "NEXO_HOME => /tmp/nexo-audit-install-XYZ/.nexo\n"
                    "NEXO_CODE => /tmp/nexo-audit-install-XYZ/.nexo\n"
                    "}\n"
                ),
                stderr="",
            )

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        check = runtime.check_launchagent_integrity()
        assert check.status == "critical"
        assert any("/private/tmp/" in item or "/tmp/" in item for item in check.evidence)

    def test_launchagent_integrity_detects_schedule_drift(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        plist_path = nexo_home / "com.nexo.catchup.plist"
        with plist_path.open("wb") as fh:
            plistlib.dump({
                "EnvironmentVariables": {
                    "NEXO_HOME": str(nexo_home),
                    "NEXO_CODE": str(nexo_home / "src"),
                },
                "StartCalendarInterval": {"Hour": 8, "Minute": 30},
            }, fh)

        monkeypatch.setattr(runtime.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(runtime, "_managed_launchagent_plists", lambda: [("catchup", plist_path)])
        monkeypatch.setattr(runtime, "_launchagent_schedule_expectations", lambda: {
            "catchup": {
                "StartInterval": None,
                "StartCalendarInterval": None,
                "RunAtLoad": True,
                "schedule_configured": True,
            }
        })
        monkeypatch.setattr(runtime.os, "getuid", lambda: 501)

        def fake_run(args, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "gui/501/com.nexo.catchup = {\n"
                    f"path = {plist_path}\n"
                    f"NEXO_HOME => {nexo_home}\n"
                    f"NEXO_CODE => {nexo_home / 'src'}\n"
                    "}\n"
                ),
                stderr="",
            )

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        check = runtime.check_launchagent_integrity()
        assert check.status == "degraded"
        assert any("schedule drift" in item for item in check.evidence)

    def test_launchagent_integrity_detects_tcc_risk_from_protected_paths(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        plist_path = nexo_home / "com.nexo.deep-sleep.plist"
        protected_code = "/Users/tester/Documents/nexo/src"
        with plist_path.open("wb") as fh:
            plistlib.dump({
                "EnvironmentVariables": {
                    "NEXO_HOME": str(nexo_home),
                    "NEXO_CODE": protected_code,
                },
                "ProgramArguments": ["/bin/bash", str(nexo_home / "scripts" / "nexo-cron-wrapper.sh"), "deep-sleep", "/bin/bash", protected_code + "/scripts/nexo-deep-sleep.sh"],
                "StartCalendarInterval": {"Hour": 4, "Minute": 30},
            }, fh)

        monkeypatch.setattr(runtime.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(runtime, "_managed_launchagent_plists", lambda: [("deep-sleep", plist_path)])
        monkeypatch.setattr(runtime, "_recent_permission_denial", lambda cron_id: cron_id == "deep-sleep")
        monkeypatch.setattr(runtime.os, "getuid", lambda: 501)
        monkeypatch.setattr(runtime, "PROTECTED_MACOS_ROOTS", (Path("/Users/tester/Documents"),))

        def fake_run(args, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "gui/501/com.nexo.deep-sleep = {\n"
                    f"path = {plist_path}\n"
                    f"NEXO_HOME => {nexo_home}\n"
                    f"NEXO_CODE => {protected_code}\n"
                    "}\n"
                ),
                stderr="",
            )

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        check = runtime.check_launchagent_integrity()
        assert check.status == "critical"
        assert any("Operation not permitted" in item for item in check.evidence)

    def test_managed_launchagents_include_run_at_load_jobs(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        launch_agents = nexo_home / "launchagents"
        launch_agents.mkdir()
        (launch_agents / "com.nexo.catchup.plist").write_text("<plist/>")

        monkeypatch.setattr(runtime, "LAUNCH_AGENTS_DIR", launch_agents)
        monkeypatch.setattr(runtime, "_launchagent_schedule_expectations", lambda: {
            "catchup": {
                "StartInterval": None,
                "StartCalendarInterval": None,
                "RunAtLoad": True,
                "schedule_configured": True,
            }
        })
        managed = runtime._managed_launchagent_plists()
        assert ("catchup", launch_agents / "com.nexo.catchup.plist") in managed

    def test_launchagent_expectations_skip_disabled_optional_jobs(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        (nexo_home / "config").mkdir(parents=True, exist_ok=True)
        (nexo_home / "config" / "optionals.json").write_text('{"autonomy": false}')
        (nexo_home / "crons" / "manifest.json").write_text(json.dumps({
            "crons": [
                {"id": "watchdog", "interval_seconds": 1800, "core": True},
                {"id": "autonomy-daemon", "keep_alive": True, "optional": "autonomy", "core": True},
            ]
        }))

        monkeypatch.setattr(runtime, "NEXO_HOME", nexo_home)
        monkeypatch.setattr(runtime, "OPTIONALS_FILE", nexo_home / "config" / "optionals.json")

        expectations = runtime._launchagent_schedule_expectations()
        assert "watchdog" in expectations
        assert "autonomy-daemon" not in expectations

    def test_launchagent_integrity_detects_keepalive_schedule_drift(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        plist_path = nexo_home / "com.nexo.personal-daemon.plist"
        with plist_path.open("wb") as fh:
            plistlib.dump({
                "EnvironmentVariables": {
                    "NEXO_HOME": str(nexo_home),
                    "NEXO_CODE": str(nexo_home),
                },
                "RunAtLoad": True,
            }, fh)

        monkeypatch.setattr(runtime.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(runtime, "_managed_launchagent_plists", lambda: [("personal-daemon", plist_path)])
        monkeypatch.setattr(runtime, "_launchagent_schedule_expectations", lambda: {
            "personal-daemon": {
                "StartInterval": None,
                "StartCalendarInterval": None,
                "RunAtLoad": True,
                "KeepAlive": True,
                "schedule_configured": True,
            }
        })
        monkeypatch.setattr(runtime.os, "getuid", lambda: 501)

        def fake_run(args, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "gui/501/com.nexo.personal-daemon = {\n"
                    f"path = {plist_path}\n"
                    f"NEXO_HOME => {nexo_home}\n"
                    f"NEXO_CODE => {nexo_home}\n"
                    "}\n"
                ),
                stderr="",
            )

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        check = runtime.check_launchagent_integrity()
        assert check.status == "degraded"
        assert any("KeepAlive" in item for item in check.evidence)

    def test_cron_freshness_ignores_personal_crons(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        (nexo_home / "config").mkdir(parents=True, exist_ok=True)
        (nexo_home / "config" / "optionals.json").write_text("{}")
        (nexo_home / "crons" / "manifest.json").write_text(json.dumps({
            "crons": [
                {"id": "watchdog", "interval_seconds": 1800, "core": True},
            ]
        }))

        conn = sqlite3.connect(str(nexo_home / "data" / "nexo.db"))
        conn.execute(
            "INSERT INTO cron_runs (cron_id, started_at) VALUES (?, ?)",
            ("watchdog", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 600))),
        )
        conn.execute(
            "INSERT INTO cron_runs (cron_id, started_at) VALUES (?, ?)",
            ("personal-backup", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 12 * 3600))),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(runtime, "NEXO_HOME", nexo_home)
        monkeypatch.setattr(runtime, "OPTIONALS_FILE", nexo_home / "config" / "optionals.json")

        check = runtime.check_cron_freshness()
        assert check.status == "healthy"

    def test_personal_script_registry_summary_includes_keep_alive_runtime(self, nexo_home, monkeypatch):
        import db
        import script_registry
        from doctor.providers import runtime

        monkeypatch.setattr(db, "init_db", lambda: None)
        monkeypatch.setattr(script_registry, "sync_personal_scripts", lambda prune_missing=True: {"ok": True})
        monkeypatch.setattr(
            db,
            "get_personal_script_health_report",
            lambda fix=False: {
                "scripts": 1,
                "schedules": 1,
                "issues": [],
                "schedule_audit": {
                    "summary": {
                        "healthy": 1,
                        "keep_alive": 1,
                        "runtime_alive": 1,
                    }
                },
            },
        )

        check = runtime.check_personal_script_registry()
        assert check.status == "healthy"
        assert "keep_alive 1/1 alive" in check.summary

    def test_personal_script_registry_degrades_on_keep_alive_runtime_issue(self, nexo_home, monkeypatch):
        import db
        import script_registry
        from doctor.providers import runtime

        monkeypatch.setattr(db, "init_db", lambda: None)
        monkeypatch.setattr(script_registry, "sync_personal_scripts", lambda prune_missing=True: {"ok": True})
        monkeypatch.setattr(
            db,
            "get_personal_script_health_report",
            lambda fix=False: {
                "scripts": 1,
                "schedules": 1,
                "issues": [
                    {"severity": "warn", "message": "keep_alive runtime wake-recovery: keep_alive service not loaded"},
                ],
                "schedule_audit": {"summary": {"healthy": 1, "keep_alive": 1, "runtime_stale": 1}},
            },
        )

        check = runtime.check_personal_script_registry()
        assert check.status == "degraded"
        assert "keep_alive runtime wake-recovery" in check.evidence[0]

    def test_client_backend_preferences_warns_when_selected_client_missing(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        schedule_file = nexo_home / "config" / "schedule.json"
        schedule_file.parent.mkdir(parents=True, exist_ok=True)
        schedule_file.write_text(json.dumps({
            "interactive_clients": {
                "claude_code": False,
                "codex": True,
                "claude_desktop": False,
            },
            "default_terminal_client": "codex",
            "automation_enabled": True,
            "automation_backend": "codex",
        }))

        monkeypatch.setattr(runtime, "SCHEDULE_FILE", schedule_file)
        monkeypatch.setattr(runtime, "detect_installed_clients", lambda user_home=None: {
            "claude_code": {"installed": True},
            "codex": {"installed": False},
            "claude_desktop": {"installed": False},
        })

        check = runtime.check_client_backend_preferences()

        assert check.status == "degraded"
        assert any("default terminal client `codex`" in item for item in check.evidence)
        assert any("automation backend `codex`" in item for item in check.evidence)

    def test_client_bootstrap_parity_warns_when_codex_bootstrap_missing(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        schedule_file = nexo_home / "config" / "schedule.json"
        schedule_file.parent.mkdir(parents=True, exist_ok=True)
        schedule_file.write_text(json.dumps({
            "interactive_clients": {
                "claude_code": False,
                "codex": True,
                "claude_desktop": False,
            },
            "default_terminal_client": "codex",
            "automation_enabled": False,
            "automation_backend": "none",
        }))

        monkeypatch.setattr(runtime, "SCHEDULE_FILE", schedule_file)
        monkeypatch.setattr(runtime, "detect_installed_clients", lambda user_home=None: {
            "claude_code": {"installed": True},
            "codex": {"installed": True},
            "claude_desktop": {"installed": False},
        })
        monkeypatch.setattr(runtime.Path, "home", lambda: nexo_home)

        check = runtime.check_client_bootstrap_parity()
        assert check.status == "degraded"
        assert any("bootstrap missing" in item for item in check.evidence)

    def test_client_bootstrap_parity_warns_when_codex_mcp_config_missing(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        schedule_file = nexo_home / "config" / "schedule.json"
        schedule_file.parent.mkdir(parents=True, exist_ok=True)
        schedule_file.write_text(json.dumps({
            "interactive_clients": {
                "claude_code": False,
                "codex": True,
                "claude_desktop": False,
            },
            "default_terminal_client": "codex",
            "automation_enabled": False,
            "automation_backend": "none",
        }))
        codex_home = nexo_home / ".codex"
        codex_home.mkdir(parents=True, exist_ok=True)
        (codex_home / "AGENTS.md").write_text(
            "<!-- nexo-codex-agents-version: 1.1.0 -->\n"
            "******CORE******\n<!-- nexo:core:start -->\ncore\n<!-- nexo:core:end -->\n"
            "******USER******\n<!-- nexo:user:start -->\nuser\n<!-- nexo:user:end -->\n"
        )
        (codex_home / "config.toml").write_text(
            'initial_messages = [{ role = "system", content = "NEXO" }]\n\n'
            '[nexo.codex]\n'
            'bootstrap_managed = true\n'
        )

        monkeypatch.setattr(runtime, "SCHEDULE_FILE", schedule_file)
        monkeypatch.setattr(runtime, "detect_installed_clients", lambda user_home=None: {
            "claude_code": {"installed": True},
            "codex": {"installed": True},
            "claude_desktop": {"installed": False},
        })
        monkeypatch.setattr(runtime.Path, "home", lambda: nexo_home)

        check = runtime.check_client_bootstrap_parity()
        assert check.status == "degraded"
        assert any("mcp_servers.nexo" in item for item in check.evidence)

    def test_protocol_compliance_uses_latest_weekly_summary(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        summary_dir = nexo_home / "operations" / "deep-sleep"
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / "2026-W14-weekly-summary.json").write_text(json.dumps({
            "label": "2026-W14",
            "protocol_summary": {
                "overall_compliance_pct": 41.2,
                "guard_check": {"required": 12, "executed": 3, "compliance_pct": 25.0},
                "heartbeat": {"total": 20, "with_context": 10, "compliance_pct": 50.0},
                "change_log": {"edits": 11, "logged": 4, "compliance_pct": 36.4},
            },
        }))

        monkeypatch.setattr(runtime, "NEXO_HOME", nexo_home)
        check = runtime.check_protocol_compliance()

        assert check.status == "critical"
        assert any("41.2%" in item for item in check.evidence)
        assert any("guard_check" in item for item in check.evidence)

    def test_release_artifact_sync_detects_version_mismatch(self, nexo_home, monkeypatch, tmp_path):
        from doctor.providers import runtime

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "scripts").mkdir()
        (repo / "package.json").write_text(json.dumps({"version": "2.7.0"}))
        (repo / "CHANGELOG.md").write_text("## [2.6.21] - 2026-04-05\n")
        sync_script = repo / "scripts" / "sync_release_artifacts.py"
        sync_script.write_text(textwrap.dedent("""\
            import sys
            if __name__ == "__main__":
                print("[sync-release-artifacts] OK")
                raise SystemExit(0)
        """))

        monkeypatch.setattr(runtime, "NEXO_CODE", repo)
        monkeypatch.setattr(runtime, "PACKAGE_JSON", repo / "package.json")
        monkeypatch.setattr(runtime, "CHANGELOG_FILE", repo / "CHANGELOG.md")

        check = runtime.check_release_artifact_sync()

        assert check.status == "critical"
        assert any("version mismatch" in item for item in check.evidence)

    def test_release_artifact_sync_uses_repo_root_when_nexo_code_points_to_src(self, nexo_home, monkeypatch, tmp_path):
        from doctor.providers import runtime

        repo = tmp_path / "repo"
        src_root = repo / "src"
        src_root.mkdir(parents=True)
        (repo / "scripts").mkdir()
        (repo / "package.json").write_text(json.dumps({"version": "3.0.0"}))
        (repo / "CHANGELOG.md").write_text("## [3.0.0] - 2026-04-06\n")
        sync_script = repo / "scripts" / "sync_release_artifacts.py"
        sync_script.write_text(textwrap.dedent("""\
            import sys
            if __name__ == "__main__":
                print("[sync-release-artifacts] OK")
                raise SystemExit(0)
        """))

        monkeypatch.setattr(runtime, "NEXO_CODE", src_root)
        monkeypatch.setattr(runtime, "PACKAGE_JSON", src_root / "package.json")
        monkeypatch.setattr(runtime, "CHANGELOG_FILE", src_root / "CHANGELOG.md")

        check = runtime.check_release_artifact_sync()

        assert check.status == "healthy"
        assert any("package version: 3.0.0" in item for item in check.evidence)
        assert any("release artifacts in sync" in item for item in check.evidence)

    def test_release_artifact_sync_uses_recorded_source_repo_for_installed_runtime(self, nexo_home, monkeypatch, tmp_path):
        from doctor.providers import runtime

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "scripts").mkdir()
        (repo / "package.json").write_text(json.dumps({"version": "3.0.0"}))
        (repo / "CHANGELOG.md").write_text("## [3.0.0] - 2026-04-06\n")
        (repo / "scripts" / "sync_release_artifacts.py").write_text(textwrap.dedent("""\
            import sys
            if __name__ == "__main__":
                print("[sync-release-artifacts] OK")
                raise SystemExit(0)
        """))

        runtime_home = tmp_path / "runtime"
        runtime_home.mkdir()
        (runtime_home / "version.json").write_text(json.dumps({"version": "3.0.0", "source": str(repo)}))

        monkeypatch.setattr(runtime, "NEXO_HOME", runtime_home)
        monkeypatch.setattr(runtime, "NEXO_CODE", runtime_home)
        monkeypatch.setattr(runtime, "PACKAGE_JSON", runtime_home / "package.json")
        monkeypatch.setattr(runtime, "CHANGELOG_FILE", runtime_home / "CHANGELOG.md")

        check = runtime.check_release_artifact_sync()

        assert check.status == "healthy"
        assert any("release artifacts in sync" in item for item in check.evidence)

    def test_transcript_source_parity_warns_when_codex_selected_without_sessions(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        schedule_file = nexo_home / "config" / "schedule.json"
        schedule_file.parent.mkdir(parents=True, exist_ok=True)
        schedule_file.write_text(json.dumps({
            "interactive_clients": {
                "claude_code": False,
                "codex": True,
                "claude_desktop": False,
            },
            "default_terminal_client": "codex",
            "automation_enabled": True,
            "automation_backend": "codex",
        }))

        monkeypatch.setattr(runtime, "SCHEDULE_FILE", schedule_file)
        monkeypatch.setattr(runtime.Path, "home", lambda: nexo_home)

        check = runtime.check_transcript_source_parity()
        assert check.status == "degraded"
        assert any("codex transcripts: missing" in item for item in check.evidence)

    def test_codex_session_parity_warns_when_recent_sessions_skip_startup(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        schedule_file = nexo_home / "config" / "schedule.json"
        schedule_file.parent.mkdir(parents=True, exist_ok=True)
        schedule_file.write_text(json.dumps({
            "interactive_clients": {
                "claude_code": False,
                "codex": True,
                "claude_desktop": False,
            },
            "default_terminal_client": "codex",
            "automation_enabled": False,
            "automation_backend": "none",
        }))
        codex_file = nexo_home / ".codex" / "sessions" / "2026" / "04" / "05" / "rollout-demo.jsonl"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        codex_file.write_text(
            json.dumps({"type": "session_meta", "payload": {"originator": "codex_cli_rs"}}) + "\n"
            + json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "hola"}}) + "\n"
        )

        monkeypatch.setattr(runtime, "SCHEDULE_FILE", schedule_file)
        monkeypatch.setattr(runtime.Path, "home", lambda: nexo_home)

        check = runtime.check_codex_session_parity()
        assert check.status == "degraded"
        assert any("nexo_startup seen in 0/" in item for item in check.evidence)

    def test_codex_conditioned_file_discipline_warns_on_read_without_protocol(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        schedule_file = nexo_home / "config" / "schedule.json"
        schedule_file.parent.mkdir(parents=True, exist_ok=True)
        schedule_file.write_text(json.dumps({
            "interactive_clients": {
                "claude_code": False,
                "codex": True,
                "claude_desktop": False,
            },
            "default_terminal_client": "codex",
            "automation_enabled": False,
            "automation_backend": "none",
        }))
        db_path = nexo_home / "data" / "nexo.db"
        _create_learnings_table(db_path)
        _create_protocol_tables(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """INSERT INTO learnings (category, title, content, status, applies_to)
               VALUES (?, ?, ?, 'active', ?)""",
            (
                "nexo-ops",
                "Review runtime.py before editing",
                "Read the conditioned rule before touching runtime.py.",
                "/repo/src/doctor/providers/runtime.py",
            ),
        )
        conn.execute(
            """INSERT INTO protocol_debt (debt_type, severity, status, created_at)
               VALUES ('codex_conditioned_read_without_protocol', 'warn', 'open', datetime('now'))"""
        )
        conn.commit()
        conn.close()

        codex_file = nexo_home / ".codex" / "sessions" / "2026" / "04" / "06" / "discipline-read.jsonl"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        codex_file.write_text(
            json.dumps({"type": "session_meta", "payload": {"originator": "codex_cli_rs", "cwd": "/repo"}}) + "\n"
            + json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": "sed -n '1,120p' src/doctor/providers/runtime.py"}),
                },
            }) + "\n"
        )

        monkeypatch.setattr(runtime, "SCHEDULE_FILE", schedule_file)
        monkeypatch.setattr(runtime.Path, "home", lambda: nexo_home)

        check = runtime.check_codex_conditioned_file_discipline()
        assert check.status == "degraded"
        assert any("read touches without protocol/guard review: 1" in item for item in check.evidence)

    def test_codex_conditioned_file_discipline_heals_old_read_only_drift_without_open_debt(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        schedule_file = nexo_home / "config" / "schedule.json"
        schedule_file.parent.mkdir(parents=True, exist_ok=True)
        schedule_file.write_text(json.dumps({
            "interactive_clients": {
                "claude_code": False,
                "codex": True,
                "claude_desktop": False,
            },
            "default_terminal_client": "codex",
            "automation_enabled": False,
            "automation_backend": "none",
        }))
        db_path = nexo_home / "data" / "nexo.db"
        _create_learnings_table(db_path)
        _create_protocol_tables(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """INSERT INTO learnings (category, title, content, status, applies_to)
               VALUES (?, ?, ?, 'active', ?)""",
            (
                "nexo-ops",
                "Review runtime.py before editing",
                "Read the conditioned rule before touching runtime.py.",
                "/repo/src/doctor/providers/runtime.py",
            ),
        )
        conn.commit()
        conn.close()

        codex_file = nexo_home / ".codex" / "sessions" / "2026" / "04" / "06" / "discipline-old-read.jsonl"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        codex_file.write_text(
            json.dumps({"type": "session_meta", "payload": {"originator": "codex_cli_rs", "cwd": "/repo"}}) + "\n"
            + json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": "sed -n '1,120p' src/doctor/providers/runtime.py"}),
                },
            }) + "\n"
        )
        old_time = time.time() - 10800
        os.utime(codex_file, (old_time, old_time))

        monkeypatch.setattr(runtime, "SCHEDULE_FILE", schedule_file)
        monkeypatch.setattr(runtime.Path, "home", lambda: nexo_home)

        check = runtime.check_codex_conditioned_file_discipline()
        assert check.status == "healthy"
        assert "Historical Codex conditioned-file drift has no open protocol debt" in check.summary

    def test_codex_conditioned_file_discipline_goes_critical_on_write_without_protocol(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        schedule_file = nexo_home / "config" / "schedule.json"
        schedule_file.parent.mkdir(parents=True, exist_ok=True)
        schedule_file.write_text(json.dumps({
            "interactive_clients": {
                "claude_code": False,
                "codex": True,
                "claude_desktop": False,
            },
            "default_terminal_client": "codex",
            "automation_enabled": False,
            "automation_backend": "none",
        }))
        db_path = nexo_home / "data" / "nexo.db"
        _create_learnings_table(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """INSERT INTO learnings (category, title, content, status, applies_to)
               VALUES (?, ?, ?, 'active', ?)""",
            (
                "nexo-ops",
                "Protocol-first edits for runtime.py",
                "Open protocol before patching runtime.py.",
                "/repo/src/doctor/providers/runtime.py",
            ),
        )
        conn.commit()
        conn.close()

        codex_file = nexo_home / ".codex" / "sessions" / "2026" / "04" / "06" / "discipline-write.jsonl"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        codex_file.write_text(
            json.dumps({"type": "session_meta", "payload": {"originator": "codex_cli_rs", "cwd": "/repo"}}) + "\n"
            + json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "apply_patch",
                    "arguments": "*** Begin Patch\n*** Update File: src/doctor/providers/runtime.py\n@@\n-old\n+new\n*** End Patch\n",
                },
            }) + "\n"
        )

        monkeypatch.setattr(runtime, "SCHEDULE_FILE", schedule_file)
        monkeypatch.setattr(runtime.Path, "home", lambda: nexo_home)

        check = runtime.check_codex_conditioned_file_discipline()
        assert check.status == "critical"
        assert any("write touches without protocol task: 1" in item for item in check.evidence)

    def test_codex_conditioned_file_discipline_accepts_protocol_open_and_guard_ack(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        schedule_file = nexo_home / "config" / "schedule.json"
        schedule_file.parent.mkdir(parents=True, exist_ok=True)
        schedule_file.write_text(json.dumps({
            "interactive_clients": {
                "claude_code": False,
                "codex": True,
                "claude_desktop": False,
            },
            "default_terminal_client": "codex",
            "automation_enabled": False,
            "automation_backend": "none",
        }))
        db_path = nexo_home / "data" / "nexo.db"
        _create_learnings_table(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """INSERT INTO learnings (category, title, content, status, applies_to)
               VALUES (?, ?, ?, 'active', ?)""",
            (
                "nexo-ops",
                "Guarded runtime.py edits",
                "Open protocol and acknowledge guard before patching runtime.py.",
                "/repo/src/doctor/providers/runtime.py",
            ),
        )
        conn.commit()
        conn.close()

        codex_file = nexo_home / ".codex" / "sessions" / "2026" / "04" / "06" / "discipline-clean.jsonl"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        codex_file.write_text(
            json.dumps({"type": "session_meta", "payload": {"originator": "codex_cli_rs", "cwd": "/repo"}}) + "\n"
            + json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "mcp__nexo__nexo_task_open",
                    "arguments": json.dumps({"goal": "Patch runtime audit", "task_type": "edit", "files": "src/doctor/providers/runtime.py"}),
                },
            }) + "\n"
            + json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "mcp__nexo__nexo_task_acknowledge_guard",
                    "arguments": json.dumps({"task_id": "task-1", "learning_ids": "41"}),
                },
            }) + "\n"
            + json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "apply_patch",
                    "arguments": "*** Begin Patch\n*** Update File: src/doctor/providers/runtime.py\n@@\n-old\n+new\n*** End Patch\n",
                },
            }) + "\n"
        )

        monkeypatch.setattr(runtime, "SCHEDULE_FILE", schedule_file)
        monkeypatch.setattr(runtime.Path, "home", lambda: nexo_home)

        check = runtime.check_codex_conditioned_file_discipline()
        assert check.status == "healthy"
        assert any("conditioned touches: 1" in item for item in check.evidence)

    def test_codex_conditioned_file_discipline_goes_critical_on_delete_without_guard_ack(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        schedule_file = nexo_home / "config" / "schedule.json"
        schedule_file.parent.mkdir(parents=True, exist_ok=True)
        schedule_file.write_text(json.dumps({
            "interactive_clients": {
                "claude_code": False,
                "codex": True,
                "claude_desktop": False,
            },
            "default_terminal_client": "codex",
            "automation_enabled": False,
            "automation_backend": "none",
        }))
        db_path = nexo_home / "data" / "nexo.db"
        _create_learnings_table(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """INSERT INTO learnings (category, title, content, status, applies_to)
               VALUES (?, ?, ?, 'active', ?)""",
            (
                "nexo-ops",
                "Guard delete on runtime.py",
                "Open protocol and acknowledge guard before deleting runtime.py.",
                "/repo/src/doctor/providers/runtime.py",
            ),
        )
        conn.commit()
        conn.close()

        codex_file = nexo_home / ".codex" / "sessions" / "2026" / "04" / "06" / "discipline-delete.jsonl"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        codex_file.write_text(
            json.dumps({"type": "session_meta", "payload": {"originator": "codex_cli_rs", "cwd": "/repo"}}) + "\n"
            + json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "mcp__nexo__nexo_task_open",
                    "arguments": json.dumps({"goal": "Delete runtime file", "task_type": "edit", "files": "src/doctor/providers/runtime.py"}),
                },
            }) + "\n"
            + json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": "rm src/doctor/providers/runtime.py"}),
                },
            }) + "\n"
        )

        monkeypatch.setattr(runtime, "SCHEDULE_FILE", schedule_file)
        monkeypatch.setattr(runtime.Path, "home", lambda: nexo_home)

        check = runtime.check_codex_conditioned_file_discipline()
        assert check.status == "critical"
        assert any("delete touches without guard acknowledgement: 1" in item for item in check.evidence)

    def test_claude_desktop_shared_brain_reports_mcp_only_mode(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        schedule_file = nexo_home / "config" / "schedule.json"
        schedule_file.parent.mkdir(parents=True, exist_ok=True)
        schedule_file.write_text(json.dumps({
            "interactive_clients": {
                "claude_code": False,
                "codex": False,
                "claude_desktop": True,
            },
            "default_terminal_client": "claude_code",
            "automation_enabled": False,
            "automation_backend": "none",
        }))
        desktop_dir = nexo_home / "Library" / "Application Support" / "Claude"
        desktop_dir.mkdir(parents=True, exist_ok=True)
        (desktop_dir / "claude_desktop_config.json").write_text(json.dumps({
            "mcpServers": {
                "nexo": {
                    "command": "/opt/homebrew/bin/python3",
                    "args": [str(nexo_home / "server.py")],
                }
            },
            "nexo": {
                "claude_desktop": {
                    "shared_brain_managed": True,
                    "shared_brain_mode": "mcp_only",
                    "managed_runtime_home": str(nexo_home),
                }
            },
        }))

        monkeypatch.setattr(runtime, "SCHEDULE_FILE", schedule_file)
        monkeypatch.setattr(runtime.Path, "home", lambda: nexo_home)
        monkeypatch.setattr(runtime, "detect_installed_clients", lambda user_home=None: {
            "claude_code": {"installed": True},
            "codex": {"installed": False},
            "claude_desktop": {"installed": True},
        })

        check = runtime.check_claude_desktop_shared_brain()
        assert check.status == "healthy"
        assert "MCP-only mode" in check.summary

    def test_client_assumption_regressions_flags_unapproved_claude_only_paths(self, nexo_home, monkeypatch, tmp_path):
        from doctor.providers import runtime

        src_root = tmp_path / "src"
        scripts_dir = src_root / "scripts" / "deep-sleep"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "collect.py").write_text("CLAUDE = '~/.claude/projects'\nCODEX='~/.codex/sessions'\nfind_codex_session_files = True\n")
        (src_root / "foo.py").write_text("ROOT='~/.claude/projects'\n")

        monkeypatch.setattr(runtime, "NEXO_CODE", tmp_path)
        check = runtime.check_client_assumption_regressions()
        assert check.status == "critical"
        assert any("foo.py" in item for item in check.evidence)

    def test_client_assumption_regressions_ignore_runtime_backups(self, nexo_home, monkeypatch, tmp_path):
        from doctor.providers import runtime

        runtime_home = tmp_path / "runtime"
        backups_dir = runtime_home / "backups" / "runtime-tree-old"
        contrib_dir = runtime_home / "contrib" / "public-core" / "repo" / "src"
        active_src = runtime_home / "src"
        backups_dir.mkdir(parents=True, exist_ok=True)
        contrib_dir.mkdir(parents=True, exist_ok=True)
        active_src.mkdir(parents=True, exist_ok=True)
        (backups_dir / "foo.py").write_text("ROOT='~/.claude/projects'\n")
        (contrib_dir / "bar.py").write_text("ROOT='~/.claude/projects'\n")
        (active_src / "scripts" / "deep-sleep").mkdir(parents=True, exist_ok=True)
        (active_src / "scripts" / "deep-sleep" / "collect.py").write_text(
            "CLAUDE='~/.claude/projects'\nCODEX='~/.codex/sessions'\nfind_codex_session_files=True\n"
        )

        monkeypatch.setattr(runtime, "NEXO_HOME", runtime_home)
        monkeypatch.setattr(runtime, "NEXO_CODE", runtime_home)

        check = runtime.check_client_assumption_regressions()

        assert check.status == "healthy"

    def test_client_assumption_regressions_allows_runtime_detector_copy(self, nexo_home, monkeypatch, tmp_path):
        from doctor.providers import runtime

        runtime_home = tmp_path / "runtime"
        src_root = runtime_home / "src"
        detector_dir = src_root / "doctor" / "providers"
        scripts_dir = src_root / "scripts" / "deep-sleep"
        detector_dir.mkdir(parents=True, exist_ok=True)
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "collect.py").write_text(
            "CLAUDE='~/.claude/projects'\nCODEX='~/.codex/sessions'\nfind_codex_session_files=True\n"
        )
        (detector_dir / "runtime.py").write_text(
            "if '.claude/projects' in text and '.codex' in text:\n    pass\n"
        )

        monkeypatch.setattr(runtime, "NEXO_HOME", runtime_home)
        monkeypatch.setattr(runtime, "NEXO_CODE", runtime_home)

        check = runtime.check_client_assumption_regressions()

        assert check.status == "healthy"

    def test_launchagent_integrity_fix_bootstraps_real_plist(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        plist_path = nexo_home / "com.nexo.watchdog.plist"
        with plist_path.open("wb") as fh:
            plistlib.dump({
                "EnvironmentVariables": {
                    "NEXO_HOME": str(nexo_home),
                    "NEXO_CODE": str(nexo_home / "src"),
                },
                "StartInterval": 1800,
            }, fh)

        monkeypatch.setattr(runtime.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(runtime, "_managed_launchagent_plists", lambda: [("watchdog", plist_path)])
        monkeypatch.setattr(runtime.os, "getuid", lambda: 501)

        calls = []
        bootstrapped = {"done": False}

        def fake_run(args, **kwargs):
            calls.append(args)
            if len(args) >= 2 and str(args[1]).endswith("sync.py"):
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")
            if args[1] == "print":
                if not bootstrapped["done"]:
                    return SimpleNamespace(returncode=1, stdout="", stderr='Could not find service "com.nexo.watchdog"')
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        "gui/501/com.nexo.watchdog = {\n"
                        f"path = {plist_path}\n"
                        f"NEXO_HOME => {nexo_home}\n"
                        f"NEXO_CODE => {nexo_home / 'src'}\n"
                        "}\n"
                    ),
                    stderr="",
                )
            if args[1] == "bootstrap":
                bootstrapped["done"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        check = runtime.check_launchagent_integrity(fix=True)
        assert check.fixed
        assert check.status == "healthy"
        assert any(args[1] == "bootstrap" for args in calls)

    def test_launchagent_integrity_fix_normalizes_special_launchagent_env(self, nexo_home, monkeypatch):
        from doctor.providers import runtime

        plist_path = nexo_home / "com.nexo.tcc-approve.plist"
        old_code = "/Users/tester/Documents/nexo/src"
        with plist_path.open("wb") as fh:
            plistlib.dump({
                "EnvironmentVariables": {
                    "NEXO_HOME": str(nexo_home),
                    "NEXO_CODE": old_code,
                },
                "ProgramArguments": ["/bin/bash", str(nexo_home / "scripts" / "nexo-tcc-approve.sh")],
                "RunAtLoad": True,
            }, fh)

        monkeypatch.setattr(runtime.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(runtime, "_managed_launchagent_plists", lambda: [("tcc-approve", plist_path)])
        monkeypatch.setattr(runtime, "_launchagent_schedule_expectations", lambda: {})
        monkeypatch.setattr(runtime, "_recent_permission_denial", lambda cron_id: False)
        monkeypatch.setattr(runtime.os, "getuid", lambda: 501)
        monkeypatch.setattr(runtime, "PROTECTED_MACOS_ROOTS", (Path("/Users/tester/Documents"),))

        calls = {"print": 0}

        def fake_run(args, **kwargs):
            if args[:2] == ["launchctl", "print"]:
                calls["print"] += 1
                current_code = old_code if calls["print"] == 1 else str(nexo_home)
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        "gui/501/com.nexo.tcc-approve = {\n"
                        f"path = {plist_path}\n"
                        f"NEXO_HOME => {nexo_home}\n"
                        f"NEXO_CODE => {current_code}\n"
                        "}\n"
                    ),
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        check = runtime.check_launchagent_integrity(fix=True)
        assert check.fixed
        with plist_path.open("rb") as fh:
            fixed = plistlib.load(fh)
        assert fixed["EnvironmentVariables"]["NEXO_CODE"] == str(nexo_home)

    def test_protocol_compliance_prefers_live_runtime_data_when_healthy(self, nexo_home):
        db_path = nexo_home / "data" / "nexo.db"
        _create_protocol_tables(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """INSERT INTO protocol_tasks (
                task_id, status, must_verify, close_evidence, must_change_log,
                change_log_id, correction_happened, learning_id, task_type,
                cortex_mode, opened_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            ("PT-1", "done", 1, "pytest passed", 1, 42, 0, None, "edit", "act"),
        )
        conn.commit()
        conn.close()

        from doctor.providers.runtime import check_protocol_compliance

        check = check_protocol_compliance()
        assert check.status == "healthy"
        assert any("overall live protocol compliance" in item for item in check.evidence)

    def test_protocol_compliance_goes_critical_on_open_error_debt(self, nexo_home):
        db_path = nexo_home / "data" / "nexo.db"
        _create_protocol_tables(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """INSERT INTO protocol_tasks (
                task_id, status, must_verify, close_evidence, must_change_log,
                change_log_id, correction_happened, learning_id, task_type,
                cortex_mode, opened_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            ("PT-2", "done", 1, "", 1, 10, 0, None, "edit", "act"),
        )
        conn.execute(
            """INSERT INTO protocol_debt (debt_type, severity, status, created_at)
               VALUES (?, ?, ?, datetime('now'))""",
            ("claimed_done_without_evidence", "error", "open"),
        )
        conn.commit()
        conn.close()

        from doctor.providers.runtime import check_protocol_compliance

        check = check_protocol_compliance()
        assert check.status == "critical"
        assert any("claimed_done_without_evidence" in item for item in check.evidence)

    def test_automation_telemetry_goes_critical_when_cost_coverage_is_missing(self, nexo_home):
        db_path = nexo_home / "data" / "nexo.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE automation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                total_cost_usd REAL,
                input_tokens INTEGER DEFAULT 0,
                cached_input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cost_source TEXT DEFAULT '',
                backend TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            """INSERT INTO automation_runs (
                total_cost_usd, input_tokens, output_tokens, cost_source, backend, created_at
            ) VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (None, 120, 20, "pricing_unavailable", "codex"),
        )
        conn.execute(
            """INSERT INTO automation_runs (
                total_cost_usd, input_tokens, output_tokens, cost_source, backend, created_at
            ) VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (0.12, 100, 10, "backend", "claude_code"),
        )
        conn.execute(
            """INSERT INTO automation_runs (
                total_cost_usd, input_tokens, output_tokens, cost_source, backend, created_at
            ) VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (0.08, 90, 12, "backend", "claude_code"),
        )
        conn.commit()
        conn.close()

        from doctor.providers.runtime import check_automation_telemetry

        check = check_automation_telemetry()
        assert check.status == "critical"
        assert any("cost_coverage=66.7%" in item for item in check.evidence)


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

    def test_self_audit_errors_are_critical(self, nexo_home):
        (nexo_home / "logs" / "self-audit-summary.json").write_text(json.dumps({
            "findings": [{"severity": "ERROR", "msg": "boom"}],
            "counts": {"error": 1, "warn": 0, "info": 0},
        }))
        from doctor.providers.deep import check_self_audit_summary
        check = check_self_audit_summary()
        assert check.status == "critical"

    def test_failed_preflight_is_critical(self, nexo_home):
        (nexo_home / "logs" / "runtime-preflight-summary.json").write_text(json.dumps({
            "ok": False,
            "checks": {"db_copy": False},
            "errors": ["db copy failed"],
        }))
        from doctor.providers.deep import check_preflight_summary
        check = check_preflight_summary()
        assert check.status == "critical"

    def test_failed_watchdog_smoke_is_critical(self, nexo_home):
        (nexo_home / "logs" / "watchdog-smoke-summary.json").write_text(json.dumps({
            "ok": False,
            "findings": [{"severity": "ERROR", "msg": "hash mismatch"}],
        }))
        from doctor.providers.deep import check_watchdog_smoke
        check = check_watchdog_smoke()
        assert check.status == "critical"


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

    def test_invalid_tier_returns_critical(self, nexo_home):
        from doctor.orchestrator import run_doctor
        report = run_doctor(tier="nonexistent")
        assert report.overall_status == "critical"
        assert len(report.checks) == 1
        assert report.checks[0].id == "orchestrator.invalid_tier"
        assert "nonexistent" in report.checks[0].summary

    def test_tier_crash_is_caught_and_reported(self, nexo_home, monkeypatch):
        from doctor import orchestrator

        def exploding_runner(fix=False):
            raise RuntimeError("provider exploded")

        monkeypatch.setitem(orchestrator._TIER_RUNNERS, "boot", exploding_runner)

        report = orchestrator.run_doctor(tier="boot")
        assert report.overall_status == "critical"
        crash_checks = [c for c in report.checks if c.id == "orchestrator.boot_crashed"]
        assert len(crash_checks) == 1
        assert "RuntimeError" in crash_checks[0].summary
        assert "provider exploded" in crash_checks[0].summary

    def test_partial_tier_crash_preserves_other_tiers(self, nexo_home, monkeypatch):
        from doctor import orchestrator

        def exploding_runtime(fix=False):
            raise ValueError("runtime blew up")

        monkeypatch.setitem(orchestrator._TIER_RUNNERS, "runtime", exploding_runtime)

        report = orchestrator.run_doctor(tier="all")
        tiers = {c.tier for c in report.checks}
        assert "boot" in tiers
        assert "deep" in tiers
        crash_checks = [c for c in report.checks if c.id == "orchestrator.runtime_crashed"]
        assert len(crash_checks) == 1


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


class TestSafeCheck:
    def test_safe_check_passes_through_normal_result(self):
        from doctor.models import DoctorCheck, safe_check

        def good_check():
            return DoctorCheck(
                id="test.good", tier="boot", status="healthy",
                severity="info", summary="All fine",
            )

        result = safe_check(good_check)
        assert result.id == "test.good"
        assert result.status == "healthy"

    def test_safe_check_catches_exception(self):
        from doctor.models import safe_check

        def bad_check():
            raise RuntimeError("kaboom")

        result = safe_check(bad_check)
        assert result.status == "critical"
        assert "bad_check" in result.id
        assert "kaboom" in result.summary
        assert result.evidence

    def test_safe_check_forwards_args_and_kwargs(self):
        from doctor.models import DoctorCheck, safe_check

        def check_with_args(fix=False):
            return DoctorCheck(
                id="test.args", tier="boot", status="healthy",
                severity="info", summary=f"fix={fix}",
            )

        result = safe_check(check_with_args, fix=True)
        assert "fix=True" in result.summary

    def test_boot_tier_survives_single_check_crash(self, nexo_home, monkeypatch):
        from doctor.providers import boot

        original_check_disk = boot.check_disk_space

        def exploding_disk_check():
            raise OSError("disk check exploded")

        monkeypatch.setattr(boot, "check_disk_space", exploding_disk_check)
        checks = boot.run_boot_checks()

        ids = [c.id for c in checks]
        assert "boot.db_exists" in ids
        assert "boot.required_dirs" in ids
        crash = [c for c in checks if "crashed" in c.id]
        assert len(crash) == 1
        assert "exploding_disk_check" in crash[0].id
        assert crash[0].status == "critical"

    def test_deep_tier_survives_single_check_crash(self, nexo_home, monkeypatch):
        from doctor.providers import deep

        def exploding_schema_check():
            raise ValueError("schema check failed")

        monkeypatch.setattr(deep, "check_schema_version", exploding_schema_check)
        checks = deep.run_deep_checks()

        crash = [c for c in checks if "crashed" in c.id]
        assert len(crash) == 1
        non_crash = [c for c in checks if "crashed" not in c.id]
        assert len(non_crash) >= 3
