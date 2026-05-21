"""Tests for script_registry — metadata parsing, runtime detection, doctor validation."""
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from db import (
    init_db,
    get_personal_script,
    list_personal_scripts,
    list_personal_script_schedules,
    sync_personal_scripts_registry,
)
from script_registry import (
    parse_inline_metadata,
    classify_runtime,
    classify_scripts_dir,
    audit_personal_schedules,
    get_declared_schedule,
    list_scripts,
    resolve_script,
    doctor_script,
    load_core_script_names,
    create_script,
    create_agent_script,
    ensure_personal_schedules,
    archive_agent,
    get_agent_status,
    list_agents,
    set_agent_schedule,
    reconcile_personal_scripts,
    rename_legacy_personal_script_filenames,
    repair_orphan_personal_schedule_metadata,
    sync_personal_scripts,
    unschedule_personal_script,
    retire_superseded_personal_scripts,
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

    config_dir = nexo_home / "config"
    config_dir.mkdir()
    (config_dir / "runtime-core-artifacts.json").write_text(json.dumps({
        "script_names": ["nexo-update.sh"],
        "hook_names": ["post-compact.sh"],
    }))

    hooks_dir = nexo_home / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "post-compact.sh").write_text("#!/bin/bash\necho hook\n")

    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("HOME", str(nexo_home))
    # Patch module-level constants
    import script_registry
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setattr(script_registry, "NEXO_HOME", nexo_home)

    # Plan Consolidado wave 2 CI pollution fix: close any shared DB
    # connection carried over from a previous test AND patch DB_PATH so
    # the next get_db() opens a fresh connection rooted in this tmp
    # NEXO_HOME. Before this block the process-global `_shared_conn`
    # and `DB_PATH` (evaluated at import time) would still point at a
    # previous test's DB — `init_db()` created the schema there but
    # the `personal_scripts` table this test inspects lived elsewhere,
    # so `list_personal_scripts()` returned [] in the full CI run.
    try:
        from db import _core as _db_core
        if _db_core._shared_conn is not None:
            try:
                _db_core._shared_conn.close()
            except Exception:
                pass
            _db_core._shared_conn = None
        monkeypatch.setattr(_db_core, "DB_PATH", str(nexo_home / "data" / "nexo.db"))
        (nexo_home / "data").mkdir(exist_ok=True)
    except Exception:
        pass

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

    def test_metadata_aliases_normalize_runtime_and_weekday_schedule(self, tmp_path):
        script = tmp_path / "weekly-shopify-health.sh"
        script.write_text(
            "#!/bin/bash\n"
            "# nexo: name=weekly-shopify-health\n"
            "# nexo: runtime=bash\n"
            "# nexo: cron_id=weekly-shopify-health\n"
            "# nexo: schedule=09:00 weekday=1\n"
            "# nexo: schedule_required=true\n"
            "echo ok\n"
        )

        meta = parse_inline_metadata(script)
        declared = get_declared_schedule(meta, "weekly-shopify-health")

        assert meta["runtime"] == "shell"
        assert meta["schedule"] == "09:00:1"
        assert declared["valid"] is True
        assert declared["schedule_label"] == "09:00 weekday=1"

    def test_agent_metadata_keys_are_parsed(self, tmp_path):
        script = tmp_path / "agent.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=reviews-agent\n"
            "# nexo: description=Reviews monitor\n"
            "# nexo: runtime=python\n"
            "# nexo: agent=true\n"
            "# nexo: agent_title=Reviews agent\n"
            "# nexo: agent_description=Checks reviews and prepares followups\n"
            "# nexo: agent_conversation_id=conv-123\n"
            "# nexo: agent_created_from=protocol-card\n"
            "# nexo: agent_archived=false\n"
            "print('ok')\n"
        )

        meta = parse_inline_metadata(script)

        assert meta["agent"] == "true"
        assert meta["agent_title"] == "Reviews agent"
        assert meta["agent_description"] == "Checks reviews and prepares followups"
        assert meta["agent_conversation_id"] == "conv-123"
        assert meta["agent_created_from"] == "protocol-card"
        assert meta["agent_archived"] == "false"


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


class TestAgentRegistry:
    def test_list_agents_filters_personal_scripts_marked_as_agent(self, scripts_dir):
        agent = scripts_dir / "ps-reviews-agent.py"
        agent.write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=reviews-agent\n"
            "# nexo: description=Reviews monitor\n"
            "# nexo: runtime=python\n"
            "# nexo: agent=true\n"
            "# nexo: agent_title=Reviews agent\n"
            "# nexo: agent_description=Checks reviews\n"
            "print('ok')\n"
        )
        regular = scripts_dir / "ps-regular.py"
        regular.write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=regular\n"
            "# nexo: runtime=python\n"
            "print('ok')\n"
        )

        rows = list_agents()

        assert [row["name"] for row in rows] == ["reviews-agent"]
        assert rows[0]["title"] == "Reviews agent"
        assert rows[0]["description"] == "Checks reviews"
        assert rows[0]["schedule_configurable"] is True
        assert rows[0]["health"] == "unknown"

    def test_agent_create_marks_scaffold_with_agent_metadata(self, scripts_dir):
        result = create_agent_script("Daily Review", description="Daily review agent", runtime="python")

        assert result["ok"] is True
        status = get_agent_status(result["name"])
        assert status["ok"] is True
        assert status["agent"]["name"] == "daily-review"
        assert status["agent"]["title"] == "Daily Review"
        assert status["agent"]["description"] == "Daily review agent"

    def test_archive_agent_hides_without_deleting_script(self, scripts_dir):
        result = create_agent_script("Archive Me", description="Disposable agent", runtime="python")
        archived = archive_agent(result["name"])

        assert archived["ok"] is True
        assert archived["archived"] is True
        assert Path(result["path"]).exists()
        assert list_agents() == []
        rows = list_agents(include_archived=True)
        assert len(rows) == 1
        assert rows[0]["archived"] is True
        assert rows[0]["enabled"] is False

    def test_archive_restore_reenables_agent_when_it_was_enabled(self, scripts_dir):
        result = create_agent_script("Restore Me", description="Restorable agent", runtime="python")

        assert archive_agent(result["name"])["ok"] is True
        restored = archive_agent(result["name"], archived=False)

        assert restored["ok"] is True
        assert restored["agent"]["archived"] is False
        assert restored["agent"]["enabled"] is True

    def test_set_agent_schedule_rejects_invalid_daily_before_writing_metadata(self, scripts_dir):
        result = create_agent_script("Bad Schedule", description="Bad schedule agent", runtime="python")

        scheduled = set_agent_schedule(result["name"], daily_at="99:99")

        assert scheduled["ok"] is False
        assert "Invalid schedule time" in scheduled["error"]
        text = Path(result["path"]).read_text()
        assert "schedule_required=true" not in text
        assert "schedule=99:99" not in text

    def test_set_agent_schedule_preserves_unknown_nexo_metadata(self, scripts_dir, monkeypatch):
        import script_registry
        from plugins import schedule as schedule_plugin

        result = create_agent_script("Future Metadata", description="Future metadata agent", runtime="python")
        path = Path(result["path"])
        path.write_text(path.read_text() + "# nexo: future_agent_key=keep-me\n")
        monkeypatch.setattr(script_registry, "_discover_personal_schedule_records", lambda: [])
        monkeypatch.setattr(schedule_plugin, "handle_schedule_add", lambda **kwargs: f"OK {kwargs.get('cron_id')}")

        scheduled = set_agent_schedule(result["name"], interval_seconds=300)

        assert scheduled["ok"] is True
        assert "# nexo: future_agent_key=keep-me" in path.read_text()

    def test_list_agents_handles_multi_calendar_schedule_records(self, scripts_dir):
        from script_registry import _agent_schedule_from_script

        schedule = _agent_schedule_from_script({
            "name": "multi-calendar",
            "metadata": {"agent": "true"},
            "schedules": [{
                "cron_id": "multi-calendar",
                "schedule_type": "calendar",
                "schedule_value": '[{"Hour": 9, "Minute": 0}, {"Hour": 17, "Minute": 30}]',
                "schedule_label": "multiple calendar entries",
            }],
        })

        assert schedule["schedule_type"] == "calendar"
        assert schedule["daily_at"] == ""
        assert schedule["effective_schedule_label"] == "multiple calendar entries"

    def test_agents_run_rejects_regular_personal_script(self, scripts_dir, monkeypatch, capsys):
        import cli

        init_db()
        regular = scripts_dir / "ps-regular.py"
        regular.write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=regular\n"
            "# nexo: runtime=python\n"
            "print('should-not-run')\n"
        )
        sync_personal_scripts()
        called = False

        def fake_scripts_run(_args):
            nonlocal called
            called = True
            return 0

        monkeypatch.setattr(cli, "_scripts_run", fake_scripts_run)
        rc = cli._agents_run(SimpleNamespace(name="regular", script_args=[]))

        captured = capsys.readouterr()
        assert rc == 1
        assert called is False
        assert "not marked as an agent" in captured.err


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

    def test_runtime_core_artifacts_mark_non_cron_core_scripts(self, scripts_dir):
        (scripts_dir / "nexo-update.sh").write_text("#!/bin/bash\necho update\n")

        entries = {entry["path"].split("/")[-1]: entry for entry in classify_scripts_dir()["entries"]}
        assert entries["nexo-update.sh"]["classification"] == "core"
        assert entries["nexo-update.sh"]["core"] is True

    def test_legacy_hook_aliases_are_not_personal(self, scripts_dir):
        (scripts_dir / "nexo-postcompact.sh").write_text("#!/bin/bash\necho legacy post compact\n")

        entries = {entry["path"].split("/")[-1]: entry for entry in classify_scripts_dir()["entries"]}
        assert entries["nexo-postcompact.sh"]["classification"] == "core"
        assert entries["nexo-postcompact.sh"]["core"] is True

    def test_non_script_artifacts_ignored(self, scripts_dir):
        (scripts_dir / "notes.csv").write_text("a,b,c\n")
        (scripts_dir / "debug.log").write_text("hello\n")
        scripts = list_scripts(include_core=False)
        assert scripts == []

    def test_internal_runtime_scripts_ignored(self, scripts_dir):
        (scripts_dir / "nexo-dashboard.sh").write_text("#!/bin/bash\necho dashboard\n")
        scripts = list_scripts(include_core=False)
        assert scripts == []

    def test_backup_artifacts_ignored(self, scripts_dir):
        (scripts_dir / "nexo-email-monitor.py.bak-20260414-030834").write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=email-monitor\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=email-monitor\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('backup')\n"
        )
        (scripts_dir / "my-tool.py").write_text("# nexo: name=my-tool\nprint('hi')\n")

        report = classify_scripts_dir()
        classes = {entry["path"].split("/")[-1]: entry["classification"] for entry in report["entries"]}
        assert classes["nexo-email-monitor.py.bak-20260414-030834"] == "ignored"

        scripts = list_scripts(include_core=False)
        names = [entry["name"] for entry in scripts]
        assert "my-tool" in names
        assert "email-monitor" not in names

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

    def test_runtime_core_manifest_marks_packaged_runtime_files_as_core(self, scripts_dir):
        config_dir = scripts_dir.parent / "config"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "runtime-core-artifacts.json").write_text(
            '{"script_names":["nexo-catchup.py"],"hook_names":["capture-tool-logs.sh","heartbeat-posttool.sh"]}\n'
        )
        (scripts_dir / "nexo-catchup.py").write_text("print('core')\n")
        (scripts_dir / "capture-tool-logs.sh").write_text("#!/bin/bash\necho core\n")
        (scripts_dir / "heartbeat-posttool.sh").write_text("#!/bin/bash\necho core\n")
        (scripts_dir / "my-tool.py").write_text("# nexo: name=my-tool\nprint('hi')\n")

        report = classify_scripts_dir()
        classes = {entry["path"].split("/")[-1]: entry["classification"] for entry in report["entries"]}
        assert classes["nexo-catchup.py"] == "core"
        assert classes["capture-tool-logs.sh"] == "core"
        assert classes["heartbeat-posttool.sh"] == "core"
        assert classes["my-tool.py"] == "personal"

    def test_packaged_core_source_overrides_poisoned_runtime_manifest(self, scripts_dir, monkeypatch):
        import script_registry

        packaged_src = scripts_dir.parent / "npm-src"
        (packaged_src / "crons").mkdir(parents=True)
        (packaged_src / "scripts").mkdir()
        (packaged_src / "hooks").mkdir()
        (packaged_src / "crons" / "manifest.json").write_text(
            '{"crons":[{"id":"immune","script":"scripts/nexo-immune.py"}]}\n'
        )
        (packaged_src / "scripts" / "nexo-immune.py").write_text("print('core')\n")
        (packaged_src / "hooks" / "capture-tool-logs.sh").write_text("#!/bin/bash\necho core\n")

        config_dir = scripts_dir.parent / "config"
        (config_dir / "runtime-core-artifacts.json").write_text(
            '{"script_names":["my-tool.py"],"hook_names":[]}\n'
        )
        (scripts_dir / "my-tool.py").write_text("# nexo: name=my-tool\nprint('hi')\n")
        monkeypatch.setattr(script_registry, "_find_packaged_core_source_dir", lambda: packaged_src)

        names = load_core_script_names()
        assert "my-tool.py" not in names
        assert "nexo-immune.py" in names

        report = classify_scripts_dir()
        classes = {entry["path"].split("/")[-1]: entry["classification"] for entry in report["entries"]}
        assert classes["my-tool.py"] == "personal"

    def test_classify_backfills_legacy_wake_recovery_metadata(self, scripts_dir):
        script = scripts_dir / "nexo-wake-recovery.sh"
        script.write_text(
            "#!/bin/bash\n"
            "# NEXO Wake Recovery — Detects sleep/wake gaps and reloads StartInterval LaunchAgents.\n"
            "# Runs as KeepAlive daemon.\n"
            "echo ok\n"
        )

        report = classify_scripts_dir()
        wake_entry = next(entry for entry in report["entries"] if entry["name"] == "wake-recovery")
        assert wake_entry["classification"] == "personal"
        assert wake_entry["declared_schedule"]["schedule_type"] == "keep_alive"
        text = script.read_text()
        assert "# nexo: cron_id=wake-recovery" in text

    def test_classify_scripts_dir_dedups_symlinked_file(self, tmp_path, monkeypatch):
        """F0.6 transitional: same physical file surfaces from two candidate
        dirs via symlink (AUDITOR-V700-PASS2 §5). Dedup by realpath keeps
        it to a single entry without hiding genuinely distinct files.
        """
        import paths
        import script_registry

        core_dir = tmp_path / "core" / "scripts"
        core_dir.mkdir(parents=True)
        real_script = core_dir / "shared-tool.sh"
        real_script.write_text(
            "#!/bin/bash\n# nexo: name=shared-tool\necho shared\n"
        )
        os.chmod(real_script, 0o755)

        personal_dir = tmp_path / "personal" / "scripts"
        personal_dir.mkdir(parents=True)
        (personal_dir / "shared-tool.sh").symlink_to(real_script)
        # A genuinely distinct file with a different name should survive.
        unique_personal = personal_dir / "my-unique.sh"
        unique_personal.write_text(
            "#!/bin/bash\n# nexo: name=my-unique\necho unique\n"
        )
        os.chmod(unique_personal, 0o755)

        core_dev_dir = tmp_path / "core-dev" / "scripts"
        core_dev_dir.mkdir(parents=True)

        monkeypatch.setattr(
            paths, "all_scripts_dirs", lambda: [core_dir, personal_dir, core_dev_dir]
        )
        monkeypatch.setattr(script_registry, "get_scripts_dir", lambda: personal_dir)
        monkeypatch.setattr(script_registry, "load_core_script_names", lambda: set())
        monkeypatch.setattr(
            script_registry, "load_core_script_identities", lambda: set()
        )
        monkeypatch.setattr(
            script_registry, "_apply_legacy_personal_script_backfills", lambda: None
        )

        report = classify_scripts_dir()
        shared_entries = [e for e in report["entries"] if e["name"] == "shared-tool"]
        assert (
            len(shared_entries) == 1
        ), f"expected realpath dedup to collapse symlink duplicates, got {shared_entries}"
        unique_entries = [e for e in report["entries"] if e["name"] == "my-unique"]
        assert len(unique_entries) == 1, "genuinely distinct files must survive dedup"


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
    # CI order-dependent pollution: these three tests pass in isolation
    # (and when run alongside several direct neighbours) but fail in the
    # full CI order because an earlier test materialises the global
    # db._core._shared_conn / DB_PATH and later calls here see an empty
    # personal_scripts because the query runs against the stale DB. The
    # scripts_dir fixture above closes the conn and monkeypatches
    # DB_PATH — that fixes local runs but not every CI ordering.
    # Tracked in NF-TEST-SCRIPT-REGISTRY-POLLUTION. strict=False so
    # either outcome is accepted while we harden the fixture and the
    # db._core module is refactored to not carry process-global state
    # across tests.
    @pytest.mark.xfail(reason="CI order-dependent db._core global state pollution — NF-TEST-SCRIPT-REGISTRY-POLLUTION", strict=False)
    def test_create_script_registers_in_db(self, scripts_dir, monkeypatch):
        import script_registry

        init_db()
        result = create_script("My Script", description="Created by test", runtime="python")
        assert result["ok"] is True
        assert result["name"] == "my-script"
        assert result["filename"] == "ps-my-script.py"
        assert result["requested_name"] == "My Script"
        assert os.path.basename(result["path"]) == "ps-my-script.py"

        registered = get_personal_script(result["path"])
        assert registered is not None
        assert registered["description"] == "Created by test"
        assert registered["runtime"] == "python"

    def test_classify_scripts_dir_marks_legacy_personal_filename_policy(self, scripts_dir):
        script = scripts_dir / "legacy-tool.py"
        script.write_text("# nexo: name=legacy-tool\nprint('hi')\n")

        report = classify_scripts_dir()
        entry = next(item for item in report["entries"] if item["name"] == "legacy-tool")
        assert entry["classification"] == "personal"
        assert entry["filename_prefixed"] is False
        assert entry["naming_policy"] == "legacy-nonprefixed"

    def test_classify_scripts_dir_marks_prefixed_personal_filename_policy(self, scripts_dir):
        script = scripts_dir / "ps-fresh-tool.py"
        script.write_text("# nexo: name=fresh-tool\nprint('hi')\n")

        report = classify_scripts_dir()
        entry = next(item for item in report["entries"] if item["name"] == "fresh-tool")
        assert entry["classification"] == "personal"
        assert entry["filename_prefixed"] is True
        assert entry["naming_policy"] == "preferred"

    def test_create_script_strips_legacy_nexo_prefix_from_personal_filename(self, scripts_dir):
        init_db()

        result = create_script("nexo-mail-poller", description="legacy name", runtime="python")

        assert result["name"] == "mail-poller"
        assert result["filename"] == "ps-mail-poller.py"
        assert os.path.basename(result["path"]) == "ps-mail-poller.py"

    def test_classify_personal_prefixed_filename_without_metadata_uses_logical_name(self, scripts_dir):
        script = scripts_dir / "ps-release-validate.sh"
        script.write_text("#!/usr/bin/env bash\necho ok\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        report = classify_scripts_dir()
        entry = next(item for item in report["entries"] if item["path"] == str(script))

        assert entry["classification"] == "personal"
        assert entry["name"] == "release-validate"

    def test_classify_scripts_dir_ignores_personal_shadow_of_core_logical_name(self, scripts_dir):
        core_dir = scripts_dir.parent / "core" / "scripts"
        core_dir.mkdir(parents=True, exist_ok=True)
        (core_dir / "nexo-morning-agent.py").write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=morning-agent\n"
            "# nexo: runtime=python\n"
            "print('core')\n"
        )
        shadow = scripts_dir / "morning-agent.py"
        shadow.write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=morning-agent\n"
            "# nexo: runtime=python\n"
            "print('legacy')\n"
        )

        report = classify_scripts_dir()
        entry = next(item for item in report["entries"] if item["path"] == str(shadow))
        assert entry["classification"] == "ignored"
        assert "core script identity" in entry["reason"]

    def test_create_script_rejects_core_logical_name_collision(self, scripts_dir):
        core_dir = scripts_dir.parent / "core" / "scripts"
        core_dir.mkdir(parents=True, exist_ok=True)
        (core_dir / "nexo-email-monitor.py").write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=email-monitor\n"
            "# nexo: runtime=python\n"
            "print('core')\n"
        )

        with pytest.raises(ValueError):
            create_script("email-monitor", description="should fail", runtime="python")

    def test_retire_superseded_personal_scripts_archives_core_shadow(self, scripts_dir):
        core_dir = scripts_dir.parent / "core" / "scripts"
        core_dir.mkdir(parents=True, exist_ok=True)
        (core_dir / "nexo-morning-agent.py").write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=morning-agent\n"
            "# nexo: runtime=python\n"
            "print('core')\n"
        )
        shadow = scripts_dir / "morning-agent.py"
        shadow.write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=morning-agent\n"
            "# nexo: runtime=python\n"
            "print('legacy')\n"
        )

        result = retire_superseded_personal_scripts()

        assert result["ok"] is True
        assert len(result["archived"]) == 1
        archived = Path(result["archived"][0]["backup_path"])
        assert not shadow.exists()
        assert archived.is_file()

    def test_rename_legacy_personal_script_filenames_normalizes_to_ps_prefix(self, scripts_dir, monkeypatch):
        import script_registry

        init_db()
        script = scripts_dir / "nexo-mail-poller.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=mail-poller\n"
            "# nexo: description=Poll operator inbox\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=mail-poller\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('ok')\n"
        )

        sync_personal_scripts_registry(
            [{
                "name": "mail-poller",
                "path": str(script),
                "runtime": "python",
                "description": "Poll operator inbox",
                "metadata": {"name": "mail-poller", "runtime": "python"},
            }],
            [{
                "cron_id": "mail-poller",
                "script_path": str(script),
                "schedule_type": "interval",
                "schedule_value": "300",
                "schedule_label": "every 300s",
                "launchd_label": "com.nexo.mail-poller",
                "plist_path": "/tmp/com.nexo.mail-poller.plist",
                "enabled": True,
                "description": "Mail poller schedule",
            }],
        )

        monkeypatch.setattr(
            script_registry,
            "_discover_personal_schedule_records",
            lambda: [{
                "cron_id": "mail-poller",
                "script_path": str(script),
                "schedule_type": "interval",
                "schedule_value": "300",
                "schedule_label": "every 300s",
                "launchd_label": "com.nexo.mail-poller",
                "plist_path": "/tmp/com.nexo.mail-poller.plist",
                "enabled": True,
                "description": "Mail poller schedule",
                "managed_marker": True,
                "script_exists": True,
                "script_within_scripts_dir": True,
            }],
        )
        monkeypatch.setattr(
            script_registry,
            "_remove_schedule_file",
            lambda **kwargs: {"cron_id": kwargs["cron_id"], "plist_path": kwargs["plist_path"], "deleted": True},
        )

        result = rename_legacy_personal_script_filenames()

        normalized = scripts_dir / "ps-mail-poller.py"
        assert result["ok"] is True
        assert result["renamed"] == [{
            "name": "mail-poller",
            "old_path": str(script),
            "new_path": str(normalized),
        }]
        assert result["unscheduled"] == [{
            "cron_id": "mail-poller",
            "plist_path": "/tmp/com.nexo.mail-poller.plist",
            "deleted": True,
        }]
        assert not script.exists()
        assert normalized.exists()
        assert "# nexo: name=mail-poller" in normalized.read_text()
        assert list_personal_script_schedules() == []

    def test_rename_legacy_personal_script_filenames_skips_when_project_atlas_still_references_name(self, scripts_dir):
        atlas_dir = scripts_dir.parent / "brain"
        atlas_dir.mkdir(parents=True, exist_ok=True)
        (atlas_dir / "project-atlas.json").write_text(json.dumps({
            "projects": [{
                "name": "legacy",
                "script": "nexo-release-validate.sh",
            }],
        }))

        script = scripts_dir / "nexo-release-validate.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            "# nexo: name=release-validate\n"
            "# nexo: runtime=shell\n"
            "echo ok\n"
        )

        result = rename_legacy_personal_script_filenames()

        assert result["renamed"] == []
        assert result["skipped"] == [{
            "name": "release-validate",
            "old_path": str(script),
            "new_path": str(scripts_dir / "ps-release-validate.sh"),
            "reason": "legacy filename still referenced by operator-owned artifacts",
            "references": [str(atlas_dir / "project-atlas.json")],
        }]
        assert script.exists()

    @pytest.mark.xfail(reason="CI order-dependent db._core global state pollution — NF-TEST-SCRIPT-REGISTRY-POLLUTION", strict=False)
    def test_sync_personal_scripts_links_schedule(self, scripts_dir, monkeypatch):
        import script_registry

        init_db()
        script = scripts_dir / "vault-sync.py"
        script.write_text(
            "# nexo: name=vault-sync\n"
            "# nexo: description=Vault sync\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=vault-sync\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('ok')\n"
        )

        monkeypatch.setattr(
            script_registry,
            "_discover_personal_schedule_records",
            lambda: [{
                "cron_id": "vault-sync",
                "script_path": str(script),
                "schedule_type": "interval",
                "schedule_value": "300",
                "schedule_label": "every 300s",
                "launchd_label": "com.nexo.vault-sync",
                "plist_path": "/tmp/com.nexo.vault-sync.plist",
                "enabled": True,
                "description": "Vault sync schedule",
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
        assert schedules[0]["cron_id"] == "vault-sync"

    @pytest.mark.xfail(reason="CI order-dependent db._core global state pollution — NF-TEST-SCRIPT-REGISTRY-POLLUTION", strict=False)
    def test_sync_personal_scripts_allows_duplicate_names_with_distinct_paths(self, scripts_dir, monkeypatch):
        init_db()
        python_script = scripts_dir / "shopify-delivery-times.py"
        shell_script = scripts_dir / "shopify-delivery-times.sh"
        python_script.write_text(
            "# nexo: name=shopify-delivery-times\n"
            "# nexo: runtime=python\n"
            "print('ok')\n"
        )
        shell_script.write_text(
            "#!/bin/bash\n"
            "# nexo: name=shopify-delivery-times\n"
            "# nexo: runtime=shell\n"
            "echo ok\n"
        )

        monkeypatch.setattr("script_registry._discover_personal_schedule_records", lambda: [])

        result = sync_personal_scripts()

        assert result["scripts_upserted"] == 2
        scripts = list_personal_scripts()
        assert len(scripts) == 2
        assert {script["path"] for script in scripts} == {str(python_script), str(shell_script)}
        assert len({script["id"] for script in scripts}) == 2

    def test_sync_personal_scripts_registry_normalizes_legacy_symlink_paths(self, scripts_dir):
        init_db()
        nexo_home = scripts_dir.parent
        legacy_home = nexo_home.parent / "claude"
        legacy_home.symlink_to(nexo_home, target_is_directory=True)

        script = scripts_dir / "mail-poller.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=mail-poller\n"
            "# nexo: runtime=python\n"
            "print('ok')\n"
        )

        legacy_record = {
            "name": "mail-poller",
            "path": str(legacy_home / "scripts" / script.name),
            "runtime": "python",
            "description": "",
            "metadata": {"name": "mail-poller", "runtime": "python"},
        }
        current_record = {
            "name": "mail-poller",
            "path": str(script),
            "runtime": "python",
            "description": "",
            "metadata": {"name": "mail-poller", "runtime": "python"},
        }

        first = sync_personal_scripts_registry([legacy_record], [])
        second = sync_personal_scripts_registry([current_record], [])

        assert first["registered_scripts"] == 1
        assert second["registered_scripts"] == 1
        scripts = list_personal_scripts()
        assert len(scripts) == 1
        assert scripts[0]["id"] == "ps-mail-poller"
        assert scripts[0]["path"] == str(script)

    def test_sync_personal_scripts_registry_repairs_stale_legacy_paths_post_f06(self, tmp_path, monkeypatch):
        nexo_home = tmp_path / "nexo"
        personal_scripts = nexo_home / "personal" / "scripts"
        core_scripts = nexo_home / "core" / "scripts"
        runtime_data = nexo_home / "runtime" / "data"
        personal_scripts.mkdir(parents=True)
        core_scripts.mkdir(parents=True)
        runtime_data.mkdir(parents=True)

        core_script = core_scripts / "nexo-email-monitor.py"
        core_script.write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=email-monitor\n"
            "# nexo: runtime=python\n"
            "print('ok')\n"
        )

        monkeypatch.setenv("NEXO_HOME", str(nexo_home))
        monkeypatch.setenv("HOME", str(tmp_path))

        import script_registry
        from db import _core as _db_core

        monkeypatch.setattr(script_registry, "NEXO_HOME", nexo_home)
        if _db_core._shared_conn is not None:
            try:
                _db_core._shared_conn.close()
            except Exception:
                pass
            _db_core._shared_conn = None
        monkeypatch.setattr(_db_core, "DB_PATH", str(runtime_data / "nexo.db"))

        init_db()
        conn = _db_core.get_db()
        conn.execute(
            """
            INSERT INTO personal_scripts (
                id, name, path, description, runtime, metadata_json, created_by, source,
                origin, enabled, has_inline_metadata, last_synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                "ps-email-monitor",
                "email-monitor",
                str(nexo_home / "scripts" / "nexo-email-monitor.py"),
                "",
                "python",
                "{}",
                "nexo-core",
                "core-toggle",
                "core",
                1,
                1,
            ),
        )
        conn.commit()

        result = sync_personal_scripts_registry([], [])
        scripts = list_personal_scripts(include_core=True)

        assert result["paths_repaired"] == [{
            "id": "ps-email-monitor",
            "old_path": str(nexo_home / "scripts" / "nexo-email-monitor.py"),
            "new_path": str(core_script),
        }]
        assert len(scripts) == 1
        assert scripts[0]["path"] == str(core_script)

    def test_ensure_personal_schedules_dry_run(self, scripts_dir, monkeypatch):
        import script_registry

        init_db()
        script = scripts_dir / "monitor.py"
        script.write_text(
            "# nexo: name=mail-poller\n"
            "# nexo: description=Poll operator inbox\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=mail-poller\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('ok')\n"
        )
        monkeypatch.setattr(script_registry, "_discover_personal_schedule_records", lambda: [])

        result = ensure_personal_schedules(dry_run=True)
        assert result["created"][0]["cron_id"] == "mail-poller"
        assert result["sync"]["missing_declared_schedules"][0]["name"] == "mail-poller"

    def test_repair_orphan_schedule_metadata_infers_launchagent_contract(self, scripts_dir, monkeypatch):
        import script_registry

        script = scripts_dir / "weekly-shopify-health.sh"
        script.write_text(
            "#!/bin/bash\n"
            "echo ok\n"
        )
        monkeypatch.setattr(
            script_registry,
            "_discover_personal_schedule_records",
            lambda: [{
                "cron_id": "weekly-shopify-health",
                "script_path": str(script),
                "schedule_type": "calendar",
                "schedule_value": '{"Hour": 9, "Minute": 0, "Weekday": 1}',
                "schedule_label": "09:00 weekday=1",
                "launchd_label": "com.nexo.weekly-shopify-health",
                "plist_path": "/tmp/com.nexo.weekly-shopify-health.plist",
                "enabled": True,
                "description": "",
                "managed_marker": False,
                "script_exists": True,
                "script_within_scripts_dir": True,
                "run_at_load": False,
            }],
        )

        result = repair_orphan_personal_schedule_metadata()
        meta = parse_inline_metadata(script)
        declared = get_declared_schedule(meta, "weekly-shopify-health")

        assert result["ok"] is True
        assert result["repaired"][0]["cron_id"] == "weekly-shopify-health"
        assert meta["runtime"] == "shell"
        assert meta["schedule"] == "09:00:1"
        assert declared["valid"] is True
        assert declared["schedule_type"] == "calendar"
        assert "# nexo: cron_id=weekly-shopify-health" in script.read_text()

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

    def test_ensure_personal_schedules_reloads_unloaded_launchagent(self, scripts_dir, monkeypatch):
        import script_registry

        init_db()
        script = scripts_dir / "mail-poller.py"
        script.write_text(
            "# nexo: name=mail-poller\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=mail-poller\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('ok')\n"
        )

        plist_path = scripts_dir / "com.nexo.mail-poller.plist"
        plist_path.write_text("plist")

        monkeypatch.setattr(
            script_registry,
            "audit_personal_schedules",
            lambda: {
                "schedules": [{
                    "cron_id": "mail-poller",
                    "script_path": str(script),
                    "schedule_type": "interval",
                    "schedule_value": "300",
                    "schedule_label": "every 300s",
                    "launchd_label": "com.nexo.mail-poller",
                    "plist_path": str(plist_path),
                    "enabled": True,
                    "schedule_managed": True,
                    "run_at_load": False,
                }],
                "summary": {},
            },
        )
        monkeypatch.setattr(script_registry.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(script_registry, "_launchctl_service_state", lambda label: {"loaded": False})
        monkeypatch.setattr(script_registry.os, "getuid", lambda: 501)

        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stderr=b"", stdout=b"")

        monkeypatch.setattr(script_registry.subprocess, "run", fake_run)

        result = ensure_personal_schedules(dry_run=False)
        entry = result["already_present"][0]
        assert entry["cron_id"] == "mail-poller"
        assert entry["reloaded"] is True
        assert entry["reason"] == "plist on disk but not loaded in launchd"
        assert any(args[1] == "bootstrap" for args in calls)

    def test_ensure_personal_schedules_reports_reload_failure(self, scripts_dir, monkeypatch):
        import script_registry

        init_db()
        script = scripts_dir / "mail-poller.py"
        script.write_text(
            "# nexo: name=mail-poller\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=mail-poller\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('ok')\n"
        )

        plist_path = scripts_dir / "com.nexo.mail-poller.plist"
        plist_path.write_text("plist")

        monkeypatch.setattr(
            script_registry,
            "audit_personal_schedules",
            lambda: {
                "schedules": [{
                    "cron_id": "mail-poller",
                    "script_path": str(script),
                    "schedule_type": "interval",
                    "schedule_value": "300",
                    "schedule_label": "every 300s",
                    "launchd_label": "com.nexo.mail-poller",
                    "plist_path": str(plist_path),
                    "enabled": True,
                    "schedule_managed": True,
                    "run_at_load": False,
                }],
                "summary": {},
            },
        )
        monkeypatch.setattr(script_registry.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(script_registry, "_launchctl_service_state", lambda label: {"loaded": False})
        monkeypatch.setattr(script_registry.os, "getuid", lambda: 501)
        monkeypatch.setattr(
            script_registry.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=b"bootstrap failed", stdout=b""),
        )

        result = ensure_personal_schedules(dry_run=False)
        entry = result["already_present"][0]
        assert entry["cron_id"] == "mail-poller"
        assert entry["reload_failed"] is True
        assert entry["reason"] == "bootstrap failed"

    def test_unschedule_personal_script_prunes_schedule(self, scripts_dir, monkeypatch):
        import script_registry

        init_db()
        script = scripts_dir / "vault-sync.py"
        script.write_text(
            "# nexo: name=vault-sync\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=vault-sync\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('ok')\n"
        )
        plist_path = scripts_dir / "com.nexo.vault-sync.plist"
        plist_path.write_text("plist")

        def _discover():
            if not plist_path.exists():
                return []
            return [{
                "cron_id": "vault-sync",
                "script_path": str(script),
                "schedule_type": "interval",
                "schedule_value": "300",
                "schedule_label": "every 300s",
                "launchd_label": "com.nexo.vault-sync",
                "plist_path": str(plist_path),
                "enabled": True,
                "description": "Vault sync schedule",
                "managed_marker": True,
                "script_exists": True,
                "script_within_scripts_dir": True,
            }]

        monkeypatch.setattr(script_registry, "_discover_personal_schedule_records", _discover)
        monkeypatch.setattr(script_registry.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(script_registry.subprocess, "run", lambda *args, **kwargs: None)

        sync_personal_scripts()
        result = unschedule_personal_script("vault-sync")
        assert result["ok"] is True
        assert result["removed_schedules"][0]["cron_id"] == "vault-sync"
        assert list_personal_script_schedules() == []

    def test_sync_reports_manual_schedule_without_blessing_it(self, scripts_dir, monkeypatch):
        import script_registry

        init_db()
        script = scripts_dir / "vault-sync.py"
        script.write_text(
            "# nexo: name=vault-sync\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=vault-sync\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('ok')\n"
        )

        monkeypatch.setattr(
            script_registry,
            "_discover_personal_schedule_records",
            lambda: [{
                "cron_id": "vault-sync",
                "script_path": str(script),
                "schedule_type": "interval",
                "schedule_value": "300",
                "schedule_label": "every 300s",
                "launchd_label": "com.nexo.vault-sync",
                "plist_path": "/tmp/com.nexo.vault-sync.plist",
                "enabled": True,
                "description": "Vault sync schedule",
                "managed_marker": False,
                "script_exists": True,
                "script_within_scripts_dir": True,
            }],
        )

        result = sync_personal_scripts()
        assert result["schedules_upserted"] == 0
        assert result["schedule_audit"]["summary"]["discovered_manual"] == 1
        assert result["missing_declared_schedules"][0]["reason"].startswith("schedule discovered but not managed")

    def test_audit_personal_schedules_marks_keep_alive_daemon_alive(self, scripts_dir, monkeypatch):
        import script_registry

        script = scripts_dir / "nexo-wake-recovery.sh"
        script.write_text(
            "#!/bin/bash\n"
            "# nexo: name=nexo-wake-recovery\n"
            "# nexo: runtime=shell\n"
            "# nexo: cron_id=wake-recovery\n"
            "# nexo: schedule_required=true\n"
            "# nexo: recovery_policy=restart_daemon\n"
            "echo ok\n"
        )

        monkeypatch.setattr(script_registry.platform, "system", lambda: "Darwin")
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
                "plist_path": "/tmp/com.nexo.wake-recovery.plist",
                "enabled": True,
                "description": "Wake recovery",
                "managed_marker": True,
                "script_exists": True,
                "script_within_scripts_dir": True,
                "run_at_load": True,
            }],
        )
        monkeypatch.setattr(
            script_registry,
            "_launchctl_service_state",
            lambda label: {"loaded": True, "pid": "123", "state": "running", "last_exit_status": "", "error": ""},
        )

        audit = audit_personal_schedules()
        record = audit["schedules"][0]

        assert record["runtime_state"] == "alive"
        assert "pid 123" in record["runtime_summary"]
        assert audit["summary"]["runtime_alive"] == 1

    def test_audit_personal_schedules_marks_duplicate_keep_alive_daemons(self, scripts_dir, monkeypatch):
        import script_registry

        script = scripts_dir / "nexo-wake-recovery.sh"
        script.write_text(
            "#!/bin/bash\n"
            "# nexo: name=nexo-wake-recovery\n"
            "# nexo: runtime=shell\n"
            "# nexo: cron_id=wake-recovery\n"
            "# nexo: schedule_required=true\n"
            "# nexo: recovery_policy=restart_daemon\n"
            "echo ok\n"
        )

        monkeypatch.setattr(script_registry.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            script_registry,
            "_discover_personal_schedule_records",
            lambda: [
                {
                    "cron_id": "wake-recovery",
                    "script_path": str(script),
                    "schedule_type": "keep_alive",
                    "schedule_value": "true",
                    "schedule_label": "keep alive",
                    "launchd_label": "com.nexo.wake-recovery",
                    "plist_path": "/tmp/com.nexo.wake-recovery-a.plist",
                    "enabled": True,
                    "description": "Wake recovery A",
                    "managed_marker": True,
                    "script_exists": True,
                    "script_within_scripts_dir": True,
                    "run_at_load": True,
                },
                {
                    "cron_id": "wake-recovery",
                    "script_path": str(script),
                    "schedule_type": "keep_alive",
                    "schedule_value": "true",
                    "schedule_label": "keep alive",
                    "launchd_label": "com.nexo.wake-recovery-2",
                    "plist_path": "/tmp/com.nexo.wake-recovery-b.plist",
                    "enabled": True,
                    "description": "Wake recovery B",
                    "managed_marker": True,
                    "script_exists": True,
                    "script_within_scripts_dir": True,
                    "run_at_load": True,
                },
            ],
        )
        monkeypatch.setattr(
            script_registry,
            "_launchctl_service_state",
            lambda label: {"loaded": True, "pid": "123", "state": "running", "last_exit_status": "", "error": ""},
        )

        audit = audit_personal_schedules()

        assert audit["summary"]["runtime_duplicated"] == 2
        assert all(item["runtime_state"] == "duplicated" for item in audit["schedules"])
        assert any("duplicate keep_alive schedules" in problem for problem in audit["schedules"][0]["runtime_problems"])

    def test_audit_personal_schedules_marks_unloaded_keep_alive_as_stale(self, scripts_dir, monkeypatch):
        import script_registry

        script = scripts_dir / "nexo-wake-recovery.sh"
        script.write_text(
            "#!/bin/bash\n"
            "# nexo: name=nexo-wake-recovery\n"
            "# nexo: runtime=shell\n"
            "# nexo: cron_id=wake-recovery\n"
            "# nexo: schedule_required=true\n"
            "# nexo: recovery_policy=restart_daemon\n"
            "echo ok\n"
        )

        monkeypatch.setattr(script_registry.platform, "system", lambda: "Darwin")
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
                "plist_path": "/tmp/com.nexo.wake-recovery.plist",
                "enabled": True,
                "description": "Wake recovery",
                "managed_marker": True,
                "script_exists": True,
                "script_within_scripts_dir": True,
                "run_at_load": True,
            }],
        )
        monkeypatch.setattr(
            script_registry,
            "_launchctl_service_state",
            lambda label: {"loaded": False, "pid": "", "state": "", "last_exit_status": "", "error": "not loaded"},
        )

        audit = audit_personal_schedules()
        record = audit["schedules"][0]

        assert record["runtime_state"] == "stale"
        assert "not loaded" in record["runtime_summary"]
        assert audit["summary"]["runtime_stale"] == 1

    def test_reconcile_personal_scripts_warns_about_inconsistent_schedule_markers(self, scripts_dir, monkeypatch):
        import script_registry

        script = scripts_dir / "monitor.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "# nexo: name=mail-poller\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=mail-poller\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('ok')\n"
        )

        monkeypatch.setattr(script_registry.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            script_registry,
            "_discover_personal_schedule_records",
            lambda: [{
                "cron_id": "mail-poller",
                "script_path": str(script),
                "schedule_type": "interval",
                "schedule_value": "300",
                "schedule_label": "every 300s",
                "launchd_label": "com.nexo.mail-poller",
                "plist_path": "/tmp/com.nexo.mail-poller.plist",
                "enabled": True,
                "description": "",
                "managed_marker": False,
                "script_exists": True,
                "script_within_scripts_dir": True,
                "run_at_load": False,
            }],
        )

        result = reconcile_personal_scripts(dry_run=True)

        assert result["marker_warnings"][0]["cron_id"] == "mail-poller"
        assert "without managed marker" in result["marker_warnings"][0]["reason"]
