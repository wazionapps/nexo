"""Tests for the CLI scripts commands — list, run, doctor, call."""
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

CLI_PY = os.path.join(os.path.dirname(__file__), "..", "src", "cli.py")


def _make_nexo_home(home: Path) -> Path:
    scripts = home / "scripts"
    scripts.mkdir(parents=True)
    for dirname in ["data", "plugins", "hooks", "coordination", "operations", "logs", "config"]:
        (home / dirname).mkdir(exist_ok=True)
    (home / "crons").mkdir(exist_ok=True)
    (home / "crons" / "manifest.json").write_text('{"crons":[]}')
    return home


@pytest.fixture
def nexo_home(tmp_path):
    """Create a temp NEXO_HOME with scripts/."""
    return _make_nexo_home(tmp_path / "nexo")


def _run_cli(nexo_home, *args, timeout=10):
    """Run cli.py with isolated NEXO_HOME."""
    env = {
        **os.environ,
        "NEXO_HOME": str(nexo_home),
        "NEXO_CODE": os.path.join(os.path.dirname(__file__), "..", "src"),
        "HOME": str(nexo_home),
    }
    result = subprocess.run(
        [sys.executable, CLI_PY, *args],
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    return result


def test_scripts_call_recovers_with_compatible_python(monkeypatch, capsys):
    import cli

    exc = ModuleNotFoundError("No module named 'fastmcp'")
    exc.name = "fastmcp"
    calls = []

    monkeypatch.delenv("NEXO_CLI_REEXECED", raising=False)
    monkeypatch.setattr(cli, "_runtime_python_candidates", lambda: ["/bad/python", "/good/python"])
    monkeypatch.setattr(cli, "_python_supports_module", lambda python_bin, module_name: python_bin == "/good/python")
    monkeypatch.setattr(cli.sys, "argv", ["cli.py", "scripts", "call", "nexo_pre_action_context", "--input", "{}"])

    def fake_run(cmd, capture_output=True, text=True, timeout=60, env=None):
        calls.append((cmd, env))
        return subprocess.CompletedProcess(cmd, 0, "RECOVERED\n", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = cli._recover_scripts_call_runtime("nexo_pre_action_context", exc)
    captured = capsys.readouterr()

    assert result == 0
    assert "RECOVERED" in captured.out
    assert calls[0][0][0] == "/good/python"
    assert calls[0][1]["NEXO_CLI_REEXECED"] == "1"


def test_doctor_prints_progress_message(nexo_home):
    result = _run_cli(nexo_home, "doctor", "--tier", "boot")
    assert "[NEXO] Inspecting boot diagnostics... please wait." in result.stderr
    assert "NEXO Doctor" in result.stdout


def test_help_prints_version_status_from_cache(nexo_home):
    (nexo_home / "config").mkdir(exist_ok=True)
    (nexo_home / "config" / "cli-version-status.json").write_text(json.dumps({
        "latest": "9.9.10",
        "checked_at": 4102444800,
    }))
    (nexo_home / "version.json").write_text(json.dumps({"version": "9.9.9"}))

    result = _run_cli(nexo_home)

    assert result.returncode == 0
    assert "NEXO Latest: v9.9.10 | Installed: v9.9.9" in result.stdout


def test_help_prefers_installed_when_cached_latest_is_older(nexo_home):
    (nexo_home / "config").mkdir(exist_ok=True)
    (nexo_home / "config" / "cli-version-status.json").write_text(json.dumps({
        "latest": "9.9.8",
        "checked_at": 4102444800,
    }))
    (nexo_home / "version.json").write_text(json.dumps({"version": "9.9.9"}))

    result = _run_cli(nexo_home)

    assert result.returncode == 0
    assert "NEXO Latest: v9.9.9 | Installed: v9.9.9" in result.stdout


class TestScriptsList:
    def test_empty_list(self, nexo_home):
        result = _run_cli(nexo_home, "scripts", "list")
        assert result.returncode == 0
        assert "No personal scripts" in result.stdout

    def test_list_personal(self, nexo_home):
        script = nexo_home / "scripts" / "my-tool.py"
        script.write_text("# nexo: name=my-tool\n# nexo: description=My tool\nprint('hi')\n")
        result = _run_cli(nexo_home, "scripts", "list")
        assert result.returncode == 0
        assert "my-tool" in result.stdout

    def test_list_json(self, nexo_home):
        script = nexo_home / "scripts" / "my-tool.py"
        script.write_text("# nexo: name=my-tool\nprint('hi')\n")
        result = _run_cli(nexo_home, "scripts", "list", "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "my-tool"


class TestScriptsCreateAndSync:
    def test_create_script(self, nexo_home):
        result = _run_cli(
            nexo_home,
            "scripts",
            "create",
            "Daily Backup",
            "--description",
            "Backup data daily",
        )
        assert result.returncode == 0
        created = nexo_home / "scripts" / "ps-daily-backup.py"
        assert created.is_file()

    def test_sync_registry_json(self, nexo_home):
        script = nexo_home / "scripts" / "my-tool.py"
        script.write_text("# nexo: name=my-tool\nprint('hi')\n")
        result = _run_cli(nexo_home, "scripts", "sync", "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert data["scripts_upserted"] == 1

    def test_schedules_empty(self, nexo_home):
        result = _run_cli(nexo_home, "scripts", "schedules", "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data == []

    def test_classify_json(self, nexo_home):
        (nexo_home / "scripts" / "my-tool.py").write_text("# nexo: name=my-tool\nprint('hi')\n")
        (nexo_home / "scripts" / "notes.txt").write_text("notes\n")
        result = _run_cli(nexo_home, "scripts", "classify", "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["summary"]["personal"] == 1
        assert data["summary"]["non-script"] == 1

    def test_reconcile_dry_run_json(self, nexo_home):
        (nexo_home / "scripts" / "monitor.py").write_text(
            "# nexo: name=email-monitor\n"
            "# nexo: runtime=python\n"
            "# nexo: cron_id=email-monitor\n"
            "# nexo: interval_seconds=300\n"
            "# nexo: schedule_required=true\n"
            "print('ok')\n"
        )
        result = _run_cli(nexo_home, "scripts", "reconcile", "--dry-run", "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["ensure_schedules"]["created"][0]["cron_id"] == "email-monitor"


class TestRuntimeUpdate:
    def test_update_uses_recorded_source_repo(self, tmp_path):
        runtime_home = tmp_path / "runtime"
        runtime_home.mkdir()
        (runtime_home / "bin").mkdir()
        (runtime_home / "db").mkdir()
        (runtime_home / ".venv" / "bin").mkdir(parents=True)
        fake_python = runtime_home / ".venv" / "bin" / "python3"
        fake_python.write_text("#!/bin/sh\nexit 0\n")
        fake_python.chmod(0o755)
        fake_pip = runtime_home / ".venv" / "bin" / "pip"
        fake_pip.write_text("#!/bin/sh\nexit 0\n")
        fake_pip.chmod(0o755)

        repo = tmp_path / "repo"
        src = repo / "src"
        src.mkdir(parents=True)
        (src / "scripts").mkdir()
        (repo / "package.json").write_text(json.dumps({"version": "9.9.9"}))

        for dirname in ["db", "cognitive", "doctor", "dashboard", "rules", "crons", "hooks", "plugins"]:
            package_dir = src / dirname
            package_dir.mkdir()
            if dirname != "plugins":
                (package_dir / "__init__.py").write_text("x = 1\n")
        for flat in [
            "server.py", "plugin_loader.py", "knowledge_graph.py", "kg_populate.py",
            "maintenance.py", "storage_router.py", "claim_graph.py", "hnsw_index.py",
            "evolution_cycle.py", "migrate_embeddings.py", "auto_close_sessions.py",
            "client_sync.py",
            "client_preferences.py", "agent_runner.py", "bootstrap_docs.py",
            "hook_guardrails.py", "protocol_settings.py", "public_evolution_queue.py",
            "auto_update.py", "tools_sessions.py", "tools_coordination.py",
            "tools_hot_context.py",
            "tools_reminders.py", "tools_reminders_crud.py", "tools_learnings.py",
            "tools_credentials.py", "tools_task_history.py", "tools_menu.py",
            "cli.py", "script_registry.py", "skills_runtime.py", "user_context.py",
            "public_contribution.py", "cron_recovery.py", "runtime_power.py",
            "requirements.txt",
        ]:
            (src / flat).write_text("x = 1\n")
        (src / "runtime_power.py").write_text(
            "def apply_power_policy(policy=None):\n"
            "    return {'ok': True, 'action': policy or 'disabled'}\n"
        )
        (src / "scripts" / "nexo-watchdog.sh").write_text("#!/bin/bash\nexit 0\n")

        (runtime_home / "version.json").write_text(json.dumps({"version": "9.9.9", "source": str(repo)}))

        env = {
            **os.environ,
            "NEXO_HOME": str(runtime_home),
            "NEXO_CODE": str(runtime_home),
            "HOME": str(tmp_path),
            "PYTHONPATH": str(runtime_home),
        }
        result = subprocess.run(
            [sys.executable, CLI_PY, "update", "--json"],
            capture_output=True, text=True, timeout=10, env=env,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["mode"] == "sync"
        assert data["source"] == str(src)
        assert (runtime_home / "db").is_dir()
        assert (runtime_home / "db" / "__init__.py").read_text() == "x = 1\n"
        assert (runtime_home / "public_contribution.py").read_text() == "x = 1\n"
        assert (runtime_home / "cron_recovery.py").read_text() == "x = 1\n"
        assert (runtime_home / "hook_guardrails.py").read_text() == "x = 1\n"
        assert (runtime_home / "protocol_settings.py").read_text() == "x = 1\n"
        assert (runtime_home / "public_evolution_queue.py").read_text() == "x = 1\n"
        assert (runtime_home / "tools_hot_context.py").read_text() == "x = 1\n"
        assert (runtime_home / "scripts" / "nexo-watchdog.sh").read_text() == "#!/bin/bash\nexit 0\n"

    def test_installed_runtime_update_repairs_missing_public_contribution_module(self, tmp_path):
        runtime_home = tmp_path / "runtime"
        runtime_home.mkdir()
        (runtime_home / "bin").mkdir()
        (runtime_home / ".venv" / "bin").mkdir(parents=True)
        fake_pip = runtime_home / ".venv" / "bin" / "pip"
        fake_pip.write_text("#!/bin/sh\nexit 0\n")
        fake_pip.chmod(0o755)

        current_src = Path(os.path.dirname(__file__)).parent / "src"
        (runtime_home / "cli.py").write_text((current_src / "cli.py").read_text())
        (runtime_home / "auto_update.py").write_text((current_src / "auto_update.py").read_text())
        (runtime_home / "runtime_home.py").write_text((current_src / "runtime_home.py").read_text())
        (runtime_home / "tree_hygiene.py").write_text((current_src / "tree_hygiene.py").read_text())
        (runtime_home / "runtime_power.py").write_text(
            "def ensure_power_policy_choice(**kwargs):\n"
            "    return {'policy': 'disabled', 'prompted': False}\n\n"
            "def apply_power_policy(policy=None):\n"
            "    return {'ok': True, 'action': policy or 'disabled', 'details': []}\n\n"
            "def format_power_policy_label(policy):\n"
            "    return policy or 'disabled'\n\n"
            "def ensure_full_disk_access_choice(**kwargs):\n"
            "    return {'status': 'unset', 'prompted': False, 'reasons': []}\n\n"
            "def format_full_disk_access_label(status):\n"
            "    return status or 'unset'\n"
        )

        repo = tmp_path / "repo"
        src = repo / "src"
        src.mkdir(parents=True)
        (repo / "package.json").write_text(json.dumps({"version": "9.9.9"}))
        for dirname in ["db", "cognitive", "doctor", "dashboard", "rules", "crons", "hooks", "plugins", "scripts", "skills"]:
            (src / dirname).mkdir()
        for dirname in ["db", "cognitive", "doctor"]:
            (src / dirname / "__init__.py").write_text(
                "def init_db():\n"
                "    return None\n" if dirname == "db" else "VALUE = 1\n"
            )
        (src / "script_registry.py").write_text(
            "def reconcile_personal_scripts(dry_run=False):\n"
            "    return {'ok': True}\n"
        )
        (src / "runtime_power.py").write_text(
            "def apply_power_policy(policy=None):\n"
            "    return {'ok': True, 'action': policy or 'disabled'}\n"
        )
        (src / "client_sync.py").write_text(
            "def sync_all_clients(**kwargs):\n"
            "    return {'ok': True, 'clients': {}}\n\n"
            "def format_sync_summary(result):\n"
            "    return 'ok'\n"
        )
        (src / "public_contribution.py").write_text(
            "def ensure_public_contribution_choice(**kwargs):\n"
            "    return {'enabled': False, 'mode': 'disabled', 'status': 'disabled', 'prompted': False}\n\n"
            "def format_public_contribution_label(config=None):\n"
            "    return 'disabled'\n\n"
            "def load_public_contribution_config():\n"
            "    return {'enabled': False, 'mode': 'disabled', 'status': 'disabled'}\n\n"
            "def refresh_public_contribution_state(config=None):\n"
            "    return config or load_public_contribution_config()\n\n"
            "def disable_public_contribution():\n"
            "    return load_public_contribution_config()\n"
        )
        (src / "crons" / "sync.py").write_text("print('ok')\n")
        (src / "scripts" / "nexo-watchdog.sh").write_text("#!/bin/sh\nexit 0\n")

        for flat in [
            "server.py", "plugin_loader.py", "knowledge_graph.py", "kg_populate.py",
            "maintenance.py", "storage_router.py", "claim_graph.py", "hnsw_index.py",
            "evolution_cycle.py", "migrate_embeddings.py", "auto_close_sessions.py",
            "client_sync.py", "client_preferences.py", "agent_runner.py", "bootstrap_docs.py", "auto_update.py", "tools_sessions.py", "tools_coordination.py",
            "tools_hot_context.py",
            "tools_reminders.py", "tools_reminders_crud.py", "tools_learnings.py",
            "tools_credentials.py", "tools_task_history.py", "tools_menu.py", "cli.py",
            "skills_runtime.py", "user_context.py", "public_contribution.py",
            "cron_recovery.py", "runtime_power.py", "requirements.txt",
        ]:
            target = src / flat
            if not target.exists():
                target.write_text("VALUE = 1\n")
        (runtime_home / "version.json").write_text(json.dumps({"version": "9.9.8", "source": str(repo)}))

        env = {
            **os.environ,
            "NEXO_HOME": str(runtime_home),
            "NEXO_CODE": str(runtime_home),
            "HOME": str(tmp_path),
            "PYTHONPATH": str(runtime_home),
        }
        result = subprocess.run(
            [sys.executable, str(runtime_home / "cli.py"), "update", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )

        assert result.returncode == 0, result.stderr
        assert "ModuleNotFoundError" not in result.stderr
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["public_contribution_mode"] == "disabled"
        assert (runtime_home / "public_contribution.py").is_file()
        assert (runtime_home / "tools_hot_context.py").is_file()

    def test_update_reports_personal_schedule_self_heal(self, tmp_path):
        runtime_home = tmp_path / "runtime"
        runtime_home.mkdir()
        (runtime_home / "bin").mkdir()
        (runtime_home / "db").mkdir()
        (runtime_home / ".venv" / "bin").mkdir(parents=True)
        fake_python = runtime_home / ".venv" / "bin" / "python3"
        fake_python.write_text("#!/bin/sh\nexit 0\n")
        fake_python.chmod(0o755)
        fake_pip = runtime_home / ".venv" / "bin" / "pip"
        fake_pip.write_text("#!/bin/sh\nexit 0\n")
        fake_pip.chmod(0o755)

        repo = tmp_path / "repo"
        src = repo / "src"
        src.mkdir(parents=True)
        (src / "scripts").mkdir()
        (repo / "package.json").write_text(json.dumps({"version": "9.9.9"}))

        for dirname in ["db", "cognitive", "doctor", "dashboard", "rules", "crons", "hooks", "plugins"]:
            package_dir = src / dirname
            package_dir.mkdir()
            if dirname != "plugins":
                (package_dir / "__init__.py").write_text("x = 1\n")
        (src / "script_registry.py").write_text(
            "def reconcile_personal_scripts(dry_run=False):\n"
            "    return {\n"
            "        'ensure_schedules': {\n"
            "            'created': [{'cron_id': 'email-monitor'}],\n"
            "            'repaired': [],\n"
            "            'invalid': [],\n"
            "        }\n"
            "    }\n"
        )
        (src / "runtime_power.py").write_text(
            "def apply_power_policy(policy=None):\n"
            "    return {'ok': True, 'action': policy or 'disabled'}\n"
        )
        (src / "client_sync.py").write_text(
            "def sync_all_clients(**kwargs):\n"
            "    return {'ok': True, 'clients': {}}\n\n"
            "def format_sync_summary(result):\n"
            "    return 'ok'\n"
        )
        (src / "public_contribution.py").write_text(
            "def ensure_public_contribution_choice(**kwargs):\n"
            "    return {'enabled': False, 'mode': 'disabled', 'status': 'disabled', 'prompted': False}\n\n"
            "def format_public_contribution_label(config=None):\n"
            "    return 'disabled'\n\n"
            "def load_public_contribution_config():\n"
            "    return {'enabled': False, 'mode': 'disabled', 'status': 'disabled'}\n\n"
            "def refresh_public_contribution_state(config=None):\n"
            "    return config or load_public_contribution_config()\n\n"
            "def disable_public_contribution():\n"
            "    return load_public_contribution_config()\n"
        )
        (src / "crons" / "sync.py").write_text("print('ok')\n")
        (src / "scripts" / "nexo-watchdog.sh").write_text("#!/bin/sh\nexit 0\n")

        for flat in [
            "server.py", "plugin_loader.py", "knowledge_graph.py", "kg_populate.py",
            "maintenance.py", "storage_router.py", "claim_graph.py", "hnsw_index.py",
            "evolution_cycle.py", "migrate_embeddings.py", "auto_close_sessions.py",
            "client_sync.py", "client_preferences.py", "agent_runner.py", "bootstrap_docs.py", "auto_update.py", "tools_sessions.py", "tools_coordination.py",
            "tools_hot_context.py",
            "tools_reminders.py", "tools_reminders_crud.py", "tools_learnings.py",
            "tools_credentials.py", "tools_task_history.py", "tools_menu.py", "cli.py",
            "skills_runtime.py", "user_context.py", "public_contribution.py",
            "cron_recovery.py", "runtime_power.py", "requirements.txt",
        ]:
            target = src / flat
            if not target.exists():
                target.write_text("VALUE = 1\n")

        (runtime_home / "version.json").write_text(json.dumps({"version": "9.9.9", "source": str(repo)}))

        env = {
            **os.environ,
            "NEXO_HOME": str(runtime_home),
            "NEXO_CODE": str(runtime_home),
            "HOME": str(tmp_path),
            "PYTHONPATH": str(runtime_home),
        }
        result = subprocess.run(
            [sys.executable, CLI_PY, "update"],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )

        assert result.returncode == 0, result.stderr
        assert "Personal schedules: self-healed 1" in result.stdout

    def test_packaged_update_reads_runtime_version_from_version_json(self, tmp_path):
        runtime_home = tmp_path / "runtime"
        plugins_dir = runtime_home / "plugins"
        plugins_dir.mkdir(parents=True)
        (runtime_home / "version.json").write_text(json.dumps({"version": "2.6.0"}))
        (runtime_home / "package.json").write_text(json.dumps({"version": "2.5.1"}))

        src_update = Path(os.path.dirname(__file__)).parent / "src" / "plugins" / "update.py"
        update_copy = plugins_dir / "update.py"
        update_copy.write_text(src_update.read_text())
        (runtime_home / "runtime_home.py").write_text(
            (Path(os.path.dirname(__file__)).parent / "src" / "runtime_home.py").read_text()
        )
        (runtime_home / "tree_hygiene.py").write_text(
            (Path(os.path.dirname(__file__)).parent / "src" / "tree_hygiene.py").read_text()
        )

        probe = (
            "import importlib.util, json, pathlib; "
            f"p = pathlib.Path({json.dumps(str(update_copy))}); "
            "spec = importlib.util.spec_from_file_location('upd', p); "
            "m = importlib.util.module_from_spec(spec); "
            "spec.loader.exec_module(m); "
            "print(json.dumps({'repo_dir': str(m.REPO_DIR), 'packaged': m._PACKAGED_INSTALL, 'version': m._read_version()}))"
        )
        env = {
            **os.environ,
            "NEXO_HOME": str(runtime_home),
            "NEXO_CODE": str(runtime_home),
            "HOME": str(tmp_path),
            "PYTHONPATH": str(runtime_home),
        }
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )

        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["packaged"] is True
        assert data["repo_dir"] == str(runtime_home)
        assert data["version"] == "2.6.0"


class TestUserDataPortability:
    def test_export_writes_portable_user_data_bundle(self, nexo_home):
        conn = sqlite3.connect(nexo_home / "data" / "nexo.db")
        conn.execute("CREATE TABLE IF NOT EXISTS marker (value TEXT)")
        conn.execute("INSERT INTO marker(value) VALUES ('exported')")
        conn.commit()
        conn.close()

        (nexo_home / "brain").mkdir()
        (nexo_home / "brain" / "session.txt").write_text("keep this\n")
        (nexo_home / "config" / "schedule.json").write_text(json.dumps({"timezone": "UTC"}))
        (nexo_home / "config" / "runtime-core-artifacts.json").write_text(json.dumps({
            "script_names": ["core-tool.sh"],
            "hook_names": [],
        }))
        (nexo_home / "scripts" / "daily-report.py").write_text("# nexo: name=daily-report\nprint('ok')\n")
        (nexo_home / "scripts" / "core-tool.sh").write_text("#!/bin/sh\nexit 0\n")

        bundle_path = nexo_home / "exports" / "user-data.tar.gz"
        result = _run_cli(nexo_home, "export", str(bundle_path), "--json")

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert bundle_path.is_file()

        with tarfile.open(bundle_path, "r:gz") as archive:
            names = set(archive.getnames())

        assert "bundle/manifest.json" in names
        assert "bundle/data/nexo.db" in names
        assert "bundle/brain/session.txt" in names
        assert "bundle/config/schedule.json" in names
        assert "bundle/personal-scripts/daily-report.py" in names
        assert "bundle/personal-scripts/core-tool.sh" not in names

    def test_import_restores_portable_user_data_bundle(self, tmp_path):
        source_home = _make_nexo_home(tmp_path / "source")
        source_conn = sqlite3.connect(source_home / "data" / "nexo.db")
        source_conn.execute("CREATE TABLE IF NOT EXISTS marker (value TEXT)")
        source_conn.execute("INSERT INTO marker(value) VALUES ('source-state')")
        source_conn.commit()
        source_conn.close()

        (source_home / "brain").mkdir()
        (source_home / "brain" / "session.txt").write_text("resume me\n")
        (source_home / "config" / "schedule.json").write_text(json.dumps({"timezone": "Europe/Madrid"}))
        (source_home / "config" / "runtime-core-artifacts.json").write_text(json.dumps({
            "script_names": [],
            "hook_names": [],
        }))
        (source_home / "scripts" / "daily-report.py").write_text("# nexo: name=daily-report\nprint('ok')\n")

        bundle_path = tmp_path / "portable-user-data.tar.gz"
        export_result = _run_cli(source_home, "export", str(bundle_path), "--json")
        assert export_result.returncode == 0, export_result.stderr

        target_home = _make_nexo_home(tmp_path / "target")
        (target_home / "config" / "schedule.json").write_text(json.dumps({"timezone": "UTC"}))

        import_result = _run_cli(target_home, "import", str(bundle_path), "--json", timeout=20)

        assert import_result.returncode == 0, import_result.stderr
        payload = json.loads(import_result.stdout)
        assert payload["ok"] is True
        assert Path(payload["safety_backup"]).is_file()
        assert (target_home / "brain" / "session.txt").read_text() == "resume me\n"
        assert json.loads((target_home / "config" / "schedule.json").read_text())["timezone"] == "Europe/Madrid"
        assert (target_home / "scripts" / "daily-report.py").is_file()

        verify_conn = sqlite3.connect(target_home / "data" / "nexo.db")
        row = verify_conn.execute("SELECT value FROM marker").fetchone()
        verify_conn.close()
        assert row[0] == "source-state"

    def test_import_rejects_path_traversal_member(self, tmp_path):
        target_home = _make_nexo_home(tmp_path / "target")
        bundle_path = tmp_path / "unsafe-portable-user-data.tar.gz"

        with tarfile.open(bundle_path, "w:gz") as archive:
            manifest = json.dumps(
                {
                    "kind": "nexo-user-data-bundle",
                    "version": "5.3.8",
                    "sections": {},
                }
            ).encode("utf-8")
            manifest_info = tarfile.TarInfo("bundle/manifest.json")
            manifest_info.size = len(manifest)
            archive.addfile(manifest_info, io.BytesIO(manifest))

            bad = b"owned\n"
            bad_info = tarfile.TarInfo("bundle/../../escape.txt")
            bad_info.size = len(bad)
            archive.addfile(bad_info, io.BytesIO(bad))

        import_result = _run_cli(target_home, "import", str(bundle_path), "--json", timeout=20)

        assert import_result.returncode == 1
        payload = json.loads(import_result.stdout)
        assert payload["ok"] is False
        assert "escapes destination" in payload["error"]
        assert not (tmp_path / "escape.txt").exists()


class TestClientsCommand:
    def test_clients_sync_writes_shared_configs(self, nexo_home, tmp_path):
        import client_sync

        fake_codex = tmp_path / "codex"
        fake_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            f"open({json.dumps(str(tmp_path / 'codex-invocation.json'))}, 'w').write(json.dumps(sys.argv[1:]))\n"
        )
        fake_codex.chmod(0o755)

        env = {
            **os.environ,
            "NEXO_HOME": str(nexo_home),
            "NEXO_CODE": os.path.join(os.path.dirname(__file__), "..", "src"),
            "HOME": str(nexo_home),
            "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        }
        result = subprocess.run(
            [sys.executable, CLI_PY, "clients", "sync", "--json"],
            capture_output=True, text=True, timeout=10, env=env,
        )

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["clients"]["claude_code"]["ok"] is True
        assert payload["clients"]["claude_desktop"]["ok"] is True
        assert payload["clients"]["codex"]["ok"] is True
        assert (nexo_home / ".claude" / "settings.json").is_file()
        assert (nexo_home / ".claude.json").is_file()
        assert client_sync._claude_desktop_config_path(nexo_home).is_file()


class TestChatCommand:
    def test_chat_launches_claude_with_current_path(self, nexo_home, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        fake_claude = tmp_path / "claude"
        out_file = tmp_path / "claude-invocation.json"
        fake_claude.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            f"open({json.dumps(str(out_file))}, 'w').write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd()}}))\n"
        )
        fake_claude.chmod(0o755)

        env = {
            **os.environ,
            "NEXO_HOME": str(nexo_home),
            "NEXO_CODE": os.path.join(os.path.dirname(__file__), "..", "src"),
            "HOME": str(nexo_home),
            "CLAUDE_BIN": str(fake_claude),
        }
        result = subprocess.run(
            [sys.executable, CLI_PY, "chat", str(workspace)],
            capture_output=True, text=True, timeout=30, env=env,
        )
        assert result.returncode == 0
        assert "[NEXO] NEXO " in result.stderr
        payload = json.loads(out_file.read_text())
        assert payload["argv"][:3] == ["--model", "claude-opus-4-6[1m]", "--dangerously-skip-permissions"]
        assert "nexo_startup" in payload["argv"][-1]
        assert "nexo_heartbeat" in payload["argv"][-1]
        assert payload["cwd"] == str(workspace.resolve())

    def test_chat_prints_version_status_from_cache(self, nexo_home, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        fake_claude = tmp_path / "claude"
        out_file = tmp_path / "claude-invocation.json"
        fake_claude.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"open({json.dumps(str(out_file))}, 'w').write(json.dumps(sys.argv[1:]))\n"
        )
        fake_claude.chmod(0o755)
        (nexo_home / "config").mkdir(exist_ok=True)
        (nexo_home / "config" / "cli-version-status.json").write_text(json.dumps({
            "latest": "9.9.10",
            "checked_at": 4102444800,
        }))
        (nexo_home / "version.json").write_text(json.dumps({"version": "9.9.9"}))

        env = {
            **os.environ,
            "NEXO_HOME": str(nexo_home),
            "NEXO_CODE": os.path.join(os.path.dirname(__file__), "..", "src"),
            "HOME": str(nexo_home),
            "CLAUDE_BIN": str(fake_claude),
        }
        result = subprocess.run(
            [sys.executable, CLI_PY, "chat", str(workspace)],
            capture_output=True, text=True, timeout=30, env=env,
        )

        assert result.returncode == 0
        assert "[NEXO] NEXO Latest: v9.9.10 | Installed: v9.9.9" in result.stderr

    def test_chat_prefers_installed_when_cached_latest_is_older(self, nexo_home, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        fake_claude = tmp_path / "claude"
        out_file = tmp_path / "claude-invocation.json"
        fake_claude.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"open({json.dumps(str(out_file))}, 'w').write(json.dumps(sys.argv[1:]))\n"
        )
        fake_claude.chmod(0o755)
        (nexo_home / "config").mkdir(exist_ok=True)
        (nexo_home / "config" / "cli-version-status.json").write_text(json.dumps({
            "latest": "9.9.8",
            "checked_at": 4102444800,
        }))
        (nexo_home / "version.json").write_text(json.dumps({"version": "9.9.9"}))

        env = {
            **os.environ,
            "NEXO_HOME": str(nexo_home),
            "NEXO_CODE": os.path.join(os.path.dirname(__file__), "..", "src"),
            "HOME": str(nexo_home),
            "CLAUDE_BIN": str(fake_claude),
        }
        result = subprocess.run(
            [sys.executable, CLI_PY, "chat", str(workspace)],
            capture_output=True, text=True, timeout=30, env=env,
        )

        assert result.returncode == 0
        assert "[NEXO] NEXO Latest: v9.9.9 | Installed: v9.9.9" in result.stderr

    def test_chat_uses_configured_codex_client(self, nexo_home, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        fake_codex = tmp_path / "codex"
        out_file = tmp_path / "codex-invocation.json"
        fake_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            f"open({json.dumps(str(out_file))}, 'w').write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd()}}))\n"
        )
        fake_codex.chmod(0o755)
        (nexo_home / "config").mkdir(exist_ok=True)
        (nexo_home / "config" / "schedule.json").write_text(json.dumps({
            "timezone": "UTC",
            "auto_update": True,
            "interactive_clients": {
                "claude_code": False,
                "codex": True,
                "claude_desktop": False,
            },
            "default_terminal_client": "codex",
            "automation_enabled": True,
            "automation_backend": "claude_code",
            "client_runtime_profiles": {
                "claude_code": {
                    "model": "claude-opus-4-6[1m]",
                    "reasoning_effort": "",
                },
                "codex": {
                    "model": "gpt-5.4",
                    "reasoning_effort": "xhigh",
                },
            },
            "processes": {},
        }))

        env = {
            **os.environ,
            "NEXO_HOME": str(nexo_home),
            "NEXO_CODE": os.path.join(os.path.dirname(__file__), "..", "src"),
            "HOME": str(nexo_home),
            "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        }
        result = subprocess.run(
            [sys.executable, CLI_PY, "chat", str(workspace)],
            capture_output=True, text=True, timeout=30, env=env,
        )
        assert result.returncode == 0
        payload = json.loads(out_file.read_text())
        argv = payload["argv"]
        assert argv[:5] == ["--sandbox", "danger-full-access", "--ask-for-approval", "never", "-c"]
        assert argv[5].startswith('initial_messages=[{role="system",content=')
        assert ["-m", "gpt-5.4"] == argv[6:8]
        assert ["-c", 'model_reasoning_effort="xhigh"'] == argv[8:10]
        assert argv[-3:-1] == ["-C", str(workspace)]
        assert "nexo_startup" in argv[-1]
        assert "nexo_heartbeat" in argv[-1]
        assert payload["cwd"] == str(workspace.resolve())

    def test_chat_prompts_when_multiple_clients_are_available_and_reorders_to_last_used(self, nexo_home, tmp_path):
        fake_claude = tmp_path / "claude"
        claude_out = tmp_path / "claude-invocation.json"
        fake_claude.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"open({json.dumps(str(claude_out))}, 'w').write(json.dumps(sys.argv[1:]))\n"
        )
        fake_claude.chmod(0o755)

        fake_codex = tmp_path / "codex"
        codex_out = tmp_path / "codex-invocation.json"
        fake_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"open({json.dumps(str(codex_out))}, 'w').write(json.dumps(sys.argv[1:]))\n"
        )
        fake_codex.chmod(0o755)

        (nexo_home / "config").mkdir(exist_ok=True)
        schedule_path = nexo_home / "config" / "schedule.json"
        schedule_path.write_text(json.dumps({
            "timezone": "UTC",
            "auto_update": True,
            "interactive_clients": {
                "claude_code": True,
                "codex": True,
                "claude_desktop": False,
            },
            "default_terminal_client": "claude_code",
            "automation_enabled": True,
            "automation_backend": "claude_code",
            "client_runtime_profiles": {
                "claude_code": {
                    "model": "claude-opus-4-6[1m]",
                    "reasoning_effort": "",
                },
                "codex": {
                    "model": "gpt-5.4",
                    "reasoning_effort": "xhigh",
                },
            },
            "processes": {},
        }))

        env = {
            **os.environ,
            "NEXO_HOME": str(nexo_home),
            "NEXO_CODE": os.path.join(os.path.dirname(__file__), "..", "src"),
            "HOME": str(nexo_home),
            "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        }

        first = subprocess.run(
            [sys.executable, CLI_PY, "chat", "."],
            input="2\n",
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert first.returncode == 0
        assert "1. Claude Code [default]" in first.stdout
        assert "2. Codex" in first.stdout
        schedule = json.loads(schedule_path.read_text())
        assert schedule["default_terminal_client"] == "claude_code"
        assert schedule["last_terminal_client"] == "codex"

        second = subprocess.run(
            [sys.executable, CLI_PY, "chat", "."],
            input="\n",
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert second.returncode == 0
        assert "1. Codex [last choice]" in second.stdout
        assert "2. Claude Code" in second.stdout
        assert codex_out.is_file()


class TestScriptsRun:
    def test_run_python_script(self, nexo_home):
        script = nexo_home / "scripts" / "hello.py"
        script.write_text("# nexo: name=hello\nimport os\nprint(f'NEXO_HOME={os.environ.get(\"NEXO_HOME\", \"?\")}')\n")
        result = _run_cli(nexo_home, "scripts", "run", "hello")
        assert result.returncode == 0
        assert f"NEXO_HOME={nexo_home}" in result.stdout

    def test_run_not_found(self, nexo_home):
        result = _run_cli(nexo_home, "scripts", "run", "nonexistent")
        assert result.returncode == 1

    def test_run_with_args(self, nexo_home):
        script = nexo_home / "scripts" / "argtest.py"
        script.write_text("# nexo: name=argtest\nimport sys\nprint(' '.join(sys.argv[1:]))\n")
        result = _run_cli(nexo_home, "scripts", "run", "argtest", "foo", "bar")
        assert result.returncode == 0
        assert "foo bar" in result.stdout

    def test_run_node_script(self, nexo_home):
        script = nexo_home / "scripts" / "hello.js"
        script.write_text("// nexo: name=hello-js\nconsole.log('hello-js')\n")
        result = _run_cli(nexo_home, "scripts", "run", "hello-js")
        assert result.returncode == 0
        assert "hello-js" in result.stdout


class TestScriptsDoctor:
    def test_doctor_clean(self, nexo_home):
        script = nexo_home / "scripts" / "clean.py"
        script.write_text("#!/usr/bin/env python3\n# nexo: name=clean\nprint('ok')\n")
        result = _run_cli(nexo_home, "scripts", "doctor", "clean")
        assert result.returncode == 0
        assert "pass" in result.stdout.lower() or "✓" in result.stdout

    def test_doctor_forbidden(self, nexo_home):
        script = nexo_home / "scripts" / "bad.py"
        script.write_text("# nexo: name=bad\nimport sqlite3\n")
        result = _run_cli(nexo_home, "scripts", "doctor", "bad")
        assert result.returncode == 1

    def test_doctor_json(self, nexo_home):
        script = nexo_home / "scripts" / "test.py"
        script.write_text("# nexo: name=test\nprint('ok')\n")
        result = _run_cli(nexo_home, "scripts", "doctor", "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)


class TestScriptsCall:
    def test_call_plugin_tool_json_output(self, nexo_home):
        (nexo_home / "data" / "nexo.db").write_text("")
        result = _run_cli(
            nexo_home,
            "scripts",
            "call",
            "nexo_doctor",
            "--input",
            json.dumps({"tier": "boot", "output": "json"}),
            "--json-output",
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "overall_status" in data
        assert "[PLUGIN LOADED]" not in result.stderr

    def test_call_unknown_tool(self, nexo_home):
        result = _run_cli(
            nexo_home,
            "scripts",
            "call",
            "does_not_exist",
            "--input",
            "{}",
        )
        assert result.returncode == 1
        assert "Tool not found" in result.stderr
