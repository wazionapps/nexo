"""Tests for the Doctor diagnostic system."""
import json
import os
import plistlib
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


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
