"""Tests for the CLI scripts commands — list, run, doctor, call."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

CLI_PY = os.path.join(os.path.dirname(__file__), "..", "src", "cli.py")


@pytest.fixture
def nexo_home(tmp_path):
    """Create a temp NEXO_HOME with scripts/."""
    home = tmp_path / "nexo"
    scripts = home / "scripts"
    scripts.mkdir(parents=True)
    for dirname in ["data", "plugins", "hooks", "coordination", "operations", "logs"]:
        (home / dirname).mkdir()
    (home / "crons").mkdir()
    (home / "crons" / "manifest.json").write_text('{"crons":[]}')
    return home


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
        created = nexo_home / "scripts" / "daily-backup.py"
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
            "auto_update.py", "tools_sessions.py", "tools_coordination.py",
            "tools_reminders.py", "tools_reminders_crud.py", "tools_learnings.py",
            "tools_credentials.py", "tools_task_history.py", "tools_menu.py",
            "cli.py", "script_registry.py", "skills_runtime.py", "user_context.py",
            "cron_recovery.py",
            "requirements.txt",
        ]:
            (src / flat).write_text("x = 1\n")
        (src / "scripts" / "nexo-watchdog.sh").write_text("#!/bin/bash\nexit 0\n")

        (runtime_home / "version.json").write_text(json.dumps({"version": "9.9.9", "source": str(repo)}))

        env = {
            **os.environ,
            "NEXO_HOME": str(runtime_home),
            "NEXO_CODE": str(runtime_home),
            "HOME": str(tmp_path),
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
        assert (runtime_home / "cron_recovery.py").read_text() == "x = 1\n"
        assert (runtime_home / "scripts" / "nexo-watchdog.sh").read_text() == "#!/bin/bash\nexit 0\n"

    def test_packaged_update_reads_runtime_version_from_version_json(self, tmp_path):
        runtime_home = tmp_path / "runtime"
        plugins_dir = runtime_home / "plugins"
        plugins_dir.mkdir(parents=True)
        (runtime_home / "version.json").write_text(json.dumps({"version": "2.6.0"}))
        (runtime_home / "package.json").write_text(json.dumps({"version": "2.5.1"}))

        src_update = Path(os.path.dirname(__file__)).parent / "src" / "plugins" / "update.py"
        update_copy = plugins_dir / "update.py"
        update_copy.write_text(src_update.read_text())

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
        assert client_sync._claude_desktop_config_path(nexo_home).is_file()


class TestChatCommand:
    def test_chat_launches_claude_with_current_path(self, nexo_home, tmp_path):
        fake_claude = tmp_path / "claude"
        out_file = tmp_path / "claude-invocation.json"
        fake_claude.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            f"open({json.dumps(str(out_file))}, 'w').write(json.dumps(sys.argv[1:]))\n"
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
            [sys.executable, CLI_PY, "chat", "."],
            capture_output=True, text=True, timeout=10, env=env,
        )
        assert result.returncode == 0
        assert json.loads(out_file.read_text()) == ["--dangerously-skip-permissions", "."]


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
