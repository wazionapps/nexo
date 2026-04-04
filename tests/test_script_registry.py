"""Tests for script_registry — metadata parsing, runtime detection, doctor validation."""
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db import init_db, get_personal_script, list_personal_scripts, list_personal_script_schedules
from script_registry import (
    parse_inline_metadata,
    classify_runtime,
    classify_scripts_dir,
    get_declared_schedule,
    list_scripts,
    resolve_script,
    doctor_script,
    load_core_script_names,
    create_script,
    ensure_personal_schedules,
    sync_personal_scripts,
    unschedule_personal_script,
)


@pytest.fixture
def scripts_dir(tmp_path, monkeypatch):
    """Create a temp NEXO_HOME with scripts/ and crons/manifest.json."""
    nexo_home = tmp_path / "nexo"
    scripts = nexo_home / "scripts"
    scripts.mkdir(parents=True)

    # Minimal manifest with one core script
    crons_dir = nexo_home / "crons"
    crons_dir.mkdir()
    (crons_dir / "manifest.json").write_text('{"crons":[{"id":"immune","script":"scripts/nexo-immune.py"}]}')

    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("HOME", str(nexo_home))
    # Patch module-level constants
    import script_registry
    monkeypatch.setattr(script_registry, "NEXO_HOME", nexo_home)

    return scripts


class TestMetadataParsing:
    def test_basic_metadata(self, tmp_path):
        script = tmp_path / "test.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=my-script\n"
            "# nexo: description=A test script\n"
            "# nexo: runtime=python\n"
            "# nexo: timeout=30\n"
            "# nexo: requires=git,rsync\n"
            "# nexo: tools=nexo_learning_search\n"
            "print('hello')\n"
        )
        meta = parse_inline_metadata(script)
        assert meta["name"] == "my-script"
        assert meta["description"] == "A test script"
        assert meta["runtime"] == "python"
        assert meta["timeout"] == "30"
        assert meta["requires"] == "git,rsync"
        assert meta["tools"] == "nexo_learning_search"

    def test_no_metadata(self, tmp_path):
        script = tmp_path / "test.py"
        script.write_text("print('no metadata')\n")
        meta = parse_inline_metadata(script)
        assert meta == {}

    def test_only_first_25_lines(self, tmp_path):
        script = tmp_path / "test.py"
        lines = ["# filler\n"] * 26 + ["# nexo: name=hidden\n"]
        script.write_text("".join(lines))
        meta = parse_inline_metadata(script)
        assert "name" not in meta

    def test_invalid_key_ignored(self, tmp_path):
        script = tmp_path / "test.py"
        script.write_text("# nexo: invalidkey=value\n# nexo: name=valid\n")
        meta = parse_inline_metadata(script)
        assert "invalidkey" not in meta
        assert meta["name"] == "valid"

    def test_js_comment_metadata(self, tmp_path):
        script = tmp_path / "test.js"
        script.write_text("// nexo: name=valid-js\n")
        meta = parse_inline_metadata(script)
        assert meta["name"] == "valid-js"

    def test_schedule_metadata(self, tmp_path):
        script = tmp_path / "test.py"
        script.write_text(
            "# nexo: name=monitor\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=monitor\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
        )
        meta = parse_inline_metadata(script)
        declared = get_declared_schedule(meta, "monitor")
        assert declared["valid"] is True
        assert declared["schedule_type"] == "interval"
        assert declared["interval_seconds"] == 300

    def test_schedule_metadata_defaults_recovery_policy(self, tmp_path):
        script = tmp_path / "mail.py"
        script.write_text(
            "# nexo: name=mail-poller\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=mail-poller\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
        )
        meta = parse_inline_metadata(script)
        declared = get_declared_schedule(meta, "mail-poller")
        assert declared["valid"] is True
        assert declared["recovery_policy"] == "run_once_on_wake"
        assert declared["run_on_wake"] is True
        assert declared["idempotent"] is True
        assert declared["max_catchup_age"] >= 1200

    def test_schedule_metadata_accepts_explicit_recovery_contract(self, tmp_path):
        script = tmp_path / "calendar.py"
        script.write_text(
            "# nexo: name=daily-review\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=daily-review\n"
            "# nexo: schedule=06:30\n"
            "# nexo: schedule_required=true\n"
            "# nexo: recovery_policy=catchup\n"
            "# nexo: run_on_boot=true\n"
            "# nexo: run_on_wake=false\n"
            "# nexo: idempotent=true\n"
            "# nexo: max_catchup_age=7200\n"
        )
        meta = parse_inline_metadata(script)
        declared = get_declared_schedule(meta, "daily-review")
        assert declared["valid"] is True
        assert declared["recovery_policy"] == "catchup"
        assert declared["run_on_boot"] is True
        assert declared["run_on_wake"] is False
        assert declared["idempotent"] is True
        assert declared["max_catchup_age"] == 7200

    def test_schedule_metadata_supports_keep_alive_restart_daemon(self, tmp_path):
        script = tmp_path / "wake.sh"
        script.write_text(
            "# nexo: name=nexo-wake-recovery\n"
            "# nexo: runtime=shell\n"
            "# nexo: cron_id=wake-recovery\n"
            "# nexo: schedule_required=true\n"
            "# nexo: recovery_policy=restart_daemon\n"
            "# nexo: run_on_boot=true\n"
            "echo ok\n"
        )
        meta = parse_inline_metadata(script)
        declared = get_declared_schedule(meta, "nexo-wake-recovery")
        assert declared["valid"] is True
        assert declared["schedule_type"] == "keep_alive"
        assert declared["schedule_label"] == "keep alive"
        assert declared["run_on_boot"] is True
        assert declared["recovery_policy"] == "restart_daemon"


class TestRuntimeDetection:
    def test_metadata_runtime(self, tmp_path):
        script = tmp_path / "test.sh"
        assert classify_runtime(script, {"runtime": "python"}) == "python"

    def test_shebang_python(self, tmp_path):
        script = tmp_path / "test"
        script.write_text("#!/usr/bin/env python3\nprint('hi')\n")
        assert classify_runtime(script, {}) == "python"

    def test_shebang_bash(self, tmp_path):
        script = tmp_path / "test"
        script.write_text("#!/bin/bash\necho hi\n")
        assert classify_runtime(script, {}) == "shell"

    def test_extension_py(self, tmp_path):
        script = tmp_path / "test.py"
        script.write_text("print('hi')\n")
        assert classify_runtime(script, {}) == "python"

    def test_extension_sh(self, tmp_path):
        script = tmp_path / "test.sh"
        script.write_text("echo hi\n")
        assert classify_runtime(script, {}) == "shell"

    def test_unknown(self, tmp_path):
        script = tmp_path / "test.rb"
        script.write_text("puts 'hi'\n")
        assert classify_runtime(script, {}) == "unknown"

    def test_extension_js(self, tmp_path):
        script = tmp_path / "test.js"
        script.write_text("console.log('hi')\n")
        assert classify_runtime(script, {}) == "node"

    def test_extension_php(self, tmp_path):
        script = tmp_path / "test.php"
        script.write_text("<?php echo 'hi';\n")
        assert classify_runtime(script, {}) == "php"


class TestCoreFiltering:
    def test_core_excluded_by_default(self, scripts_dir):
        # Create a core script
        (scripts_dir / "nexo-immune.py").write_text("# core script\n")
        # Create a personal script
        (scripts_dir / "my-backup.py").write_text("# nexo: name=my-backup\nprint('hi')\n")

        scripts = list_scripts(include_core=False)
        names = [s["name"] for s in scripts]
        assert "my-backup" in names
        assert "nexo-immune" not in names

    def test_core_included_with_flag(self, scripts_dir):
        (scripts_dir / "nexo-immune.py").write_text("# core script\n")
        (scripts_dir / "my-backup.py").write_text("# nexo: name=my-backup\nprint('hi')\n")

        scripts = list_scripts(include_core=True)
        names = [s["name"] for s in scripts]
        assert "my-backup" in names
        assert "nexo-immune" in names

    def test_non_script_artifacts_ignored(self, scripts_dir):
        (scripts_dir / "notes.csv").write_text("a,b,c\n")
        (scripts_dir / "debug.log").write_text("hello\n")
        scripts = list_scripts(include_core=False)
        assert scripts == []

    def test_internal_runtime_scripts_ignored(self, scripts_dir):
        (scripts_dir / "nexo-dashboard.sh").write_text("#!/bin/bash\necho dashboard\n")
        scripts = list_scripts(include_core=False)
        assert scripts == []

    def test_classify_scripts_dir(self, scripts_dir):
        (scripts_dir / "nexo-immune.py").write_text("# core script\n")
        (scripts_dir / "my-tool.py").write_text("# nexo: name=my-tool\nprint('hi')\n")
        (scripts_dir / "notes.csv").write_text("a,b,c\n")
        (scripts_dir / "nexo-dashboard.sh").write_text("#!/bin/bash\necho dashboard\n")

        report = classify_scripts_dir()
        classes = {entry["name"]: entry["classification"] for entry in report["entries"]}
        assert classes["nexo-immune"] == "core"
        assert classes["my-tool"] == "personal"
        assert classes["notes"] == "non-script"
        assert classes["nexo-dashboard"] == "ignored"

    def test_classify_backfills_legacy_wake_recovery_metadata(self, scripts_dir):
        script = scripts_dir / "nexo-wake-recovery.sh"
        script.write_text(
            "#!/bin/bash\n"
            "# NEXO Wake Recovery — Detects sleep/wake gaps and reloads StartInterval LaunchAgents.\n"
            "# Runs as KeepAlive daemon.\n"
            "echo ok\n"
        )

        report = classify_scripts_dir()
        wake_entry = next(entry for entry in report["entries"] if entry["name"] == "nexo-wake-recovery")
        assert wake_entry["classification"] == "personal"
        assert wake_entry["declared_schedule"]["schedule_type"] == "keep_alive"
        text = script.read_text()
        assert "# nexo: cron_id=wake-recovery" in text


class TestResolveScript:
    def test_resolve_by_name(self, scripts_dir):
        (scripts_dir / "my-tool.py").write_text("# nexo: name=my-tool\n# nexo: description=A tool\n")
        info = resolve_script("my-tool")
        assert info is not None
        assert info["name"] == "my-tool"

    def test_resolve_by_stem(self, scripts_dir):
        (scripts_dir / "quick-check.sh").write_text("#!/bin/bash\necho ok\n")
        info = resolve_script("quick-check")
        assert info is not None

    def test_resolve_not_found(self, scripts_dir):
        assert resolve_script("nonexistent") is None


class TestDoctorScript:
    def test_healthy_script(self, scripts_dir):
        script = scripts_dir / "good.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=good\n"
            "# nexo: runtime=python\n"
            "# nexo: timeout=30\n"
            "print('hello')\n"
        )
        result = doctor_script(str(script))
        assert result["status"] == "pass"

    def test_forbidden_pattern(self, scripts_dir):
        script = scripts_dir / "bad.py"
        script.write_text(
            "# nexo: name=bad\n"
            "import sqlite3\n"
            "conn = sqlite3.connect('nexo.db')\n"
        )
        result = doctor_script(str(script))
        assert result["status"] == "fail"
        fail_msgs = [i["msg"] for i in result["items"] if i["level"] == "fail"]
        assert any("sqlite3" in m for m in fail_msgs)

    def test_missing_executable_bit(self, scripts_dir):
        script = scripts_dir / "noexec.sh"
        script.write_text("#!/bin/bash\necho hello\n")
        os.chmod(str(script), 0o644)  # no exec bit
        result = doctor_script(str(script))
        warn_msgs = [i["msg"] for i in result["items"] if i["level"] == "warn"]
        assert any("executable" in m.lower() for m in warn_msgs)

    def test_not_found(self, scripts_dir):
        result = doctor_script("nonexistent")
        assert result["status"] == "fail"


class TestRegistrySync:
    def test_create_script_registers_in_db(self, scripts_dir, monkeypatch):
        import script_registry

        init_db()
        result = create_script("My Script", description="Created by test", runtime="python")
        assert result["ok"] is True

        registered = get_personal_script(result["path"])
        assert registered is not None
        assert registered["description"] == "Created by test"
        assert registered["runtime"] == "python"

    def test_sync_personal_scripts_links_schedule(self, scripts_dir, monkeypatch):
        import script_registry

        init_db()
        script = scripts_dir / "backup.py"
        script.write_text(
            "# nexo: name=backup\n"
            "# nexo: description=Backup\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=backup\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('ok')\n"
        )

        monkeypatch.setattr(
            script_registry,
            "_discover_personal_schedule_records",
            lambda: [{
                "cron_id": "backup",
                "script_path": str(script),
                "schedule_type": "interval",
                "schedule_value": "300",
                "schedule_label": "every 300s",
                "launchd_label": "com.nexo.backup",
                "plist_path": "/tmp/com.nexo.backup.plist",
                "enabled": True,
                "description": "Backup schedule",
                "managed_marker": True,
                "script_exists": True,
                "script_within_scripts_dir": True,
            }],
        )

        result = sync_personal_scripts()
        assert result["scripts_upserted"] == 1
        assert result["schedules_upserted"] == 1

        scripts = list_personal_scripts()
        assert len(scripts) == 1
        assert scripts[0]["has_schedule"] is True
        schedules = list_personal_script_schedules()
        assert len(schedules) == 1
        assert schedules[0]["cron_id"] == "backup"

    def test_ensure_personal_schedules_dry_run(self, scripts_dir, monkeypatch):
        import script_registry

        init_db()
        script = scripts_dir / "monitor.py"
        script.write_text(
            "# nexo: name=email-monitor\n"
            "# nexo: description=Monitor inbox\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=email-monitor\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('ok')\n"
        )
        monkeypatch.setattr(script_registry, "_discover_personal_schedule_records", lambda: [])

        result = ensure_personal_schedules(dry_run=True)
        assert result["created"][0]["cron_id"] == "email-monitor"
        assert result["sync"]["missing_declared_schedules"][0]["name"] == "email-monitor"

    def test_ensure_personal_keep_alive_schedule_repairs_manual_daemon(self, scripts_dir, monkeypatch):
        import script_registry
        from plugins import schedule as schedule_plugin

        init_db()
        script = scripts_dir / "nexo-wake-recovery.sh"
        script.write_text(
            "#!/bin/bash\n"
            "# nexo: name=nexo-wake-recovery\n"
            "# nexo: runtime=shell\n"
            "# nexo: cron_id=wake-recovery\n"
            "# nexo: schedule_required=true\n"
            "# nexo: recovery_policy=restart_daemon\n"
            "# nexo: run_on_boot=true\n"
            "echo ok\n"
        )

        plist_path = scripts_dir / "com.nexo.wake-recovery.plist"
        plist_path.write_text("plist")

        monkeypatch.setattr(
            script_registry,
            "_discover_personal_schedule_records",
            lambda: [{
                "cron_id": "wake-recovery",
                "script_path": str(script),
                "schedule_type": "keep_alive",
                "schedule_value": "true",
                "schedule_label": "keep alive",
                "launchd_label": "com.nexo.wake-recovery",
                "plist_path": str(plist_path),
                "enabled": True,
                "description": "Wake recovery",
                "managed_marker": False,
                "script_exists": True,
                "script_within_scripts_dir": True,
                "run_at_load": True,
            }],
        )
        monkeypatch.setattr(script_registry, "_remove_schedule_file", lambda **kwargs: {"cron_id": kwargs["cron_id"], "deleted": True})
        monkeypatch.setattr(
            schedule_plugin,
            "handle_schedule_add",
            lambda **kwargs: f"keep_alive={kwargs.get('keep_alive')} cron_id={kwargs.get('cron_id')}",
        )

        result = ensure_personal_schedules(dry_run=False)
        assert result["repaired"][0]["cron_id"] == "wake-recovery"
        assert "keep_alive=True" in result["repaired"][0]["result"]

    def test_unschedule_personal_script_prunes_schedule(self, scripts_dir, monkeypatch):
        import script_registry

        init_db()
        script = scripts_dir / "backup.py"
        script.write_text(
            "# nexo: name=backup\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=backup\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('ok')\n"
        )
        plist_path = scripts_dir / "com.nexo.backup.plist"
        plist_path.write_text("plist")

        def _discover():
            if not plist_path.exists():
                return []
            return [{
                "cron_id": "backup",
                "script_path": str(script),
                "schedule_type": "interval",
                "schedule_value": "300",
                "schedule_label": "every 300s",
                "launchd_label": "com.nexo.backup",
                "plist_path": str(plist_path),
                "enabled": True,
                "description": "Backup schedule",
                "managed_marker": True,
                "script_exists": True,
                "script_within_scripts_dir": True,
            }]

        monkeypatch.setattr(script_registry, "_discover_personal_schedule_records", _discover)
        monkeypatch.setattr(script_registry.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(script_registry.subprocess, "run", lambda *args, **kwargs: None)

        sync_personal_scripts()
        result = unschedule_personal_script("backup")
        assert result["ok"] is True
        assert result["removed_schedules"][0]["cron_id"] == "backup"
        assert list_personal_script_schedules() == []

    def test_sync_reports_manual_schedule_without_blessing_it(self, scripts_dir, monkeypatch):
        import script_registry

        init_db()
        script = scripts_dir / "backup.py"
        script.write_text(
            "# nexo: name=backup\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=backup\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('ok')\n"
        )

        monkeypatch.setattr(
            script_registry,
            "_discover_personal_schedule_records",
            lambda: [{
                "cron_id": "backup",
                "script_path": str(script),
                "schedule_type": "interval",
                "schedule_value": "300",
                "schedule_label": "every 300s",
                "launchd_label": "com.nexo.backup",
                "plist_path": "/tmp/com.nexo.backup.plist",
                "enabled": True,
                "description": "Backup schedule",
                "managed_marker": False,
                "script_exists": True,
                "script_within_scripts_dir": True,
            }],
        )

        result = sync_personal_scripts()
        assert result["schedules_upserted"] == 0
        assert result["schedule_audit"]["summary"]["discovered_manual"] == 1
        assert result["missing_declared_schedules"][0]["reason"].startswith("schedule discovered but not managed")
