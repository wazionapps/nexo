"""Tests for the CLI scripts commands — list, run, doctor, call."""
import json
import os
import subprocess
import sys

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


class TestRuntimeUpdate:
    def test_update_uses_recorded_source_repo(self, tmp_path):
        runtime_home = tmp_path / "runtime"
        runtime_home.mkdir()
        (runtime_home / "bin").mkdir()

        repo = tmp_path / "repo"
        src = repo / "src"
        src.mkdir(parents=True)
        (repo / "package.json").write_text(json.dumps({"version": "9.9.9"}))

        for dirname in ["db", "cognitive", "doctor", "dashboard", "rules", "crons", "hooks", "plugins"]:
            (src / dirname).mkdir()
        for flat in [
            "server.py", "plugin_loader.py", "knowledge_graph.py", "kg_populate.py",
            "maintenance.py", "storage_router.py", "claim_graph.py", "hnsw_index.py",
            "evolution_cycle.py", "migrate_embeddings.py", "auto_close_sessions.py",
            "auto_update.py", "tools_sessions.py", "tools_coordination.py",
            "tools_reminders.py", "tools_reminders_crud.py", "tools_learnings.py",
            "tools_credentials.py", "tools_task_history.py", "tools_menu.py",
            "cli.py", "script_registry.py", "skills_runtime.py", "user_context.py",
            "requirements.txt",
        ]:
            (src / flat).write_text("x = 1\n")

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
