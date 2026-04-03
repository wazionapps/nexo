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
