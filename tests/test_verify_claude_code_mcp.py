from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_claude_code_mcp.py"
SPEC = importlib.util.spec_from_file_location("verify_claude_code_mcp", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _base_server(runtime_root: Path) -> dict:
    return {
        "command": str(runtime_root / ".venv" / "bin" / "python3"),
        "args": [str(runtime_root / "server.py")],
        "env": {
            "NEXO_HOME": str(runtime_root),
            "NEXO_CODE": str(runtime_root),
            "NEXO_NAME": "NEXO",
        },
    }


def _prepare_runtime(home: Path, runtime_root: Path) -> None:
    (runtime_root / ".venv" / "bin").mkdir(parents=True)
    (runtime_root / ".venv" / "bin" / "python3").write_text("")
    (runtime_root / "server.py").write_text("print('server')\n")
    (runtime_root / "data").mkdir(parents=True)
    (runtime_root / "data" / "nexo.db").write_text("db")
    _write_json(home / ".claude.json", {"mcpServers": {"nexo": _base_server(runtime_root)}})
    _write_json(home / ".claude" / "settings.json", {"mcpServers": {"nexo": _base_server(runtime_root)}})


def test_inspect_ok_when_root_settings_and_cli_match(tmp_path):
    home = tmp_path / "home"
    runtime_root = home / ".nexo"
    _prepare_runtime(home, runtime_root)

    report = MODULE.inspect_claude_code_mcp(
        home=home,
        workspace=tmp_path,
        cli_output=f"nexo: {runtime_root / '.venv' / 'bin' / 'python3'} {runtime_root / 'server.py'} - ✓ Connected\n",
    )

    assert report["ok"] is True
    assert report["issues"] == []
    assert str(runtime_root / "data" / "nexo.db") == report["active_db"]


def test_inspect_fails_when_root_is_missing_but_settings_has_server(tmp_path):
    home = tmp_path / "home"
    runtime_root = home / ".nexo"
    _prepare_runtime(home, runtime_root)
    _write_json(home / ".claude.json", {})

    report = MODULE.inspect_claude_code_mcp(
        home=home,
        workspace=tmp_path,
        cli_output="",
        cli_returncode=1,
        cli_stderr="boom",
    )

    assert report["ok"] is False
    assert any(".claude.json" in issue and "settings.json" in issue for issue in report["issues"])


def test_inspect_fails_on_root_settings_drift(tmp_path):
    home = tmp_path / "home"
    runtime_root = home / ".nexo"
    _prepare_runtime(home, runtime_root)
    drifted = _base_server(runtime_root)
    drifted["args"] = [str(runtime_root / "other-server.py")]
    _write_json(home / ".claude" / "settings.json", {"mcpServers": {"nexo": drifted}})

    report = MODULE.inspect_claude_code_mcp(
        home=home,
        workspace=tmp_path,
        cli_output=f"nexo: {runtime_root / '.venv' / 'bin' / 'python3'} {runtime_root / 'server.py'} - ✓ Connected\n",
    )

    assert report["ok"] is False
    assert any("desincronizado" in issue for issue in report["issues"])


def test_inspect_fails_on_legacy_home_and_db(tmp_path):
    home = tmp_path / "home"
    managed = home / ".nexo"
    legacy = home / "claude"
    _prepare_runtime(home, managed)
    (legacy / ".venv" / "bin").mkdir(parents=True)
    (legacy / ".venv" / "bin" / "python3").write_text("")
    (legacy / "server.py").write_text("print('legacy')\n")
    (legacy / "data").mkdir(parents=True)
    (legacy / "data" / "nexo.db").write_text("legacy-db")
    legacy_server = {
        "command": str(legacy / ".venv" / "bin" / "python3"),
        "args": [str(legacy / "server.py")],
        "env": {
            "NEXO_HOME": str(legacy),
            "NEXO_CODE": str(legacy),
        },
    }
    _write_json(home / ".claude.json", {"mcpServers": {"nexo": legacy_server}})
    _write_json(home / ".claude" / "settings.json", {"mcpServers": {"nexo": legacy_server}})

    report = MODULE.inspect_claude_code_mcp(
        home=home,
        workspace=tmp_path,
        cli_output=f"nexo: {legacy / '.venv' / 'bin' / 'python3'} {legacy / 'server.py'} - ✓ Connected\n",
    )

    assert report["ok"] is False
    assert any("legacy" in issue for issue in report["issues"])


def test_inspect_fails_when_workspace_override_differs(tmp_path):
    home = tmp_path / "home"
    runtime_root = home / ".nexo"
    _prepare_runtime(home, runtime_root)
    workspace = tmp_path / "workspace" / "project"
    workspace.mkdir(parents=True)
    local_server = _base_server(runtime_root)
    local_server["args"] = [str(runtime_root / "workspace-server.py")]
    _write_json(workspace / ".mcp.json", {"mcpServers": {"nexo": local_server}})

    report = MODULE.inspect_claude_code_mcp(
        home=home,
        workspace=workspace,
        cli_output=f"nexo: {runtime_root / '.venv' / 'bin' / 'python3'} {runtime_root / 'server.py'} - ✓ Connected\n",
    )

    assert report["ok"] is False
    assert any("workspace" in issue.lower() for issue in report["issues"])


def test_inspect_fails_when_cli_loads_another_server(tmp_path):
    home = tmp_path / "home"
    runtime_root = home / ".nexo"
    _prepare_runtime(home, runtime_root)

    report = MODULE.inspect_claude_code_mcp(
        home=home,
        workspace=tmp_path,
        cli_output="nexo: /tmp/python /tmp/server.py - ✓ Connected\n",
    )

    assert report["ok"] is False
    assert any("claude mcp list" in issue for issue in report["issues"])
