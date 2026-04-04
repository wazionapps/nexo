"""Tests for shared client sync across Claude Code, Claude Desktop, and Codex."""

import json
import os
import subprocess
import sys
from pathlib import Path


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_runtime(root: Path) -> Path:
    runtime = root / "runtime"
    (runtime / ".venv" / "bin").mkdir(parents=True)
    (runtime / ".venv" / "bin" / "python3").write_text("")
    (runtime / "server.py").write_text("print('server')\n")
    (runtime / "version.json").write_text(json.dumps({"operator_name": "Atlas"}))
    return runtime


def test_sync_claude_code_preserves_existing_settings(tmp_path):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "mcpServers": {"other": {"command": "node", "args": ["other.js"]}},
        "hooks": {"SessionStart": [{"matcher": "*", "hooks": []}]},
    }))

    result = client_sync.sync_claude_code(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    payload = json.loads(settings_path.read_text())
    assert payload["hooks"]["SessionStart"][0]["matcher"] == "*"
    assert payload["mcpServers"]["other"]["command"] == "node"
    assert payload["mcpServers"]["nexo"]["args"] == [str(runtime / "server.py")]
    assert payload["mcpServers"]["nexo"]["env"]["NEXO_HOME"] == str(runtime)
    assert payload["mcpServers"]["nexo"]["env"]["NEXO_CODE"] == str(runtime)
    assert payload["mcpServers"]["nexo"]["env"]["NEXO_NAME"] == "Atlas"


def test_sync_claude_desktop_preserves_preferences(tmp_path):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"
    desktop_path = client_sync._claude_desktop_config_path(home)
    desktop_path.parent.mkdir(parents=True)
    desktop_path.write_text(json.dumps({
        "preferences": {"sidebarMode": "chat"},
        "mcpServers": {"legacy": {"command": "python3", "args": ["legacy.py"]}},
    }))

    result = client_sync.sync_claude_desktop(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    payload = json.loads(desktop_path.read_text())
    assert payload["preferences"]["sidebarMode"] == "chat"
    assert payload["mcpServers"]["legacy"]["args"] == ["legacy.py"]
    assert payload["mcpServers"]["nexo"]["env"]["NEXO_NAME"] == "Atlas"


def test_sync_codex_uses_codex_cli_when_available(tmp_path, monkeypatch):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"
    captured = {}

    monkeypatch.setattr(client_sync.shutil, "which", lambda name: "/tmp/fake-codex" if name == "codex" else None)

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, "Added global MCP server 'nexo'.", "")

    monkeypatch.setattr(client_sync.subprocess, "run", fake_run)

    result = client_sync.sync_codex(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    assert captured["cmd"][:4] == ["/tmp/fake-codex", "mcp", "add", "nexo"]
    assert f"NEXO_HOME={runtime}" in captured["cmd"]
    assert f"NEXO_CODE={runtime}" in captured["cmd"]
    assert "NEXO_NAME=Atlas" in captured["cmd"]
    assert captured["cmd"][-2:] == [str(runtime / ".venv" / "bin" / "python3"), str(runtime / "server.py")]
    assert captured["env"]["HOME"] == str(home)


def test_sync_all_clients_treats_missing_codex_as_non_fatal(tmp_path, monkeypatch):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"

    monkeypatch.setattr(client_sync.shutil, "which", lambda name: None)

    result = client_sync.sync_all_clients(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    assert result["clients"]["codex"]["skipped"] is True
    assert "codex binary not found" in result["clients"]["codex"]["reason"]
