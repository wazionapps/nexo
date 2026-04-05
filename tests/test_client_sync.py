"""Tests for shared client sync across Claude Code, Claude Desktop, and Codex."""

import json
import os
import subprocess
import sys
from pathlib import Path


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def _make_runtime(root: Path, *, operator_name: str = "Atlas") -> Path:
    runtime = root / "runtime"
    (runtime / ".venv" / "bin").mkdir(parents=True)
    (runtime / ".venv" / "bin" / "python3").write_text("")
    (runtime / "server.py").write_text("print('server')\n")
    payload = {}
    if operator_name is not None:
        payload["operator_name"] = operator_name
    (runtime / "version.json").write_text(json.dumps(payload))
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
    bootstrap_path = home / ".claude" / "CLAUDE.md"
    assert bootstrap_path.is_file()
    bootstrap_text = bootstrap_path.read_text()
    assert "******CORE******" in bootstrap_text
    assert "******USER******" in bootstrap_text
    assert "Evolution" in bootstrap_text


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
    assert payload["nexo"]["claude_desktop"]["shared_brain_managed"] is True
    assert payload["nexo"]["claude_desktop"]["shared_brain_mode"] == "mcp_only"


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
    bootstrap_path = home / ".codex" / "AGENTS.md"
    assert bootstrap_path.is_file()
    bootstrap_text = bootstrap_path.read_text()
    assert "******CORE******" in bootstrap_text
    assert "******USER******" in bootstrap_text
    assert "NEXO Shared Brain for Codex" in bootstrap_text
    config_path = home / ".codex" / "config.toml"
    config_text = config_path.read_text()
    assert 'model = "gpt-5.4"' in config_text
    assert 'model_reasoning_effort = "xhigh"' in config_text
    assert "initial_messages = [{ role = \"system\"" in config_text
    assert "[nexo.codex]" in config_text
    assert "bootstrap_managed = true" in config_text
    assert "mcp_managed = true" in config_text
    assert "[mcp_servers.nexo]" in config_text


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
    assert (home / ".codex" / "AGENTS.md").is_file()
    assert "[mcp_servers.nexo]" in (home / ".codex" / "config.toml").read_text()


def test_sync_codex_defaults_operator_name_when_runtime_version_has_blank_value(tmp_path, monkeypatch):
    import client_sync

    runtime = _make_runtime(tmp_path, operator_name="")
    home = tmp_path / "home"
    captured = {}

    monkeypatch.setattr(client_sync.shutil, "which", lambda name: "/tmp/fake-codex" if name == "codex" else None)

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "Added global MCP server 'nexo'.", "")

    monkeypatch.setattr(client_sync.subprocess, "run", fake_run)

    result = client_sync.sync_codex(
        nexo_home=runtime,
        runtime_root=runtime,
        user_home=home,
    )

    assert result["ok"] is True
    assert "NEXO_NAME=NEXO" in captured["cmd"]
    bootstrap_text = (home / ".codex" / "AGENTS.md").read_text()
    assert "You are NEXO" in bootstrap_text


def test_sync_codex_falls_back_to_managed_config_when_cli_add_fails(tmp_path, monkeypatch):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"

    monkeypatch.setattr(client_sync.shutil, "which", lambda name: "/tmp/fake-codex" if name == "codex" else None)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "mcp add exploded")

    monkeypatch.setattr(client_sync.subprocess, "run", fake_run)

    result = client_sync.sync_codex(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    assert result["mode"] == "config_only"
    assert "mcp add exploded" in result["warning"]
    config_text = (home / ".codex" / "config.toml").read_text()
    assert "[mcp_servers.nexo]" in config_text


def test_sync_all_clients_can_limit_to_configured_clients(tmp_path, monkeypatch):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"

    monkeypatch.setattr(client_sync.shutil, "which", lambda name: None)

    result = client_sync.sync_all_clients(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
        preferences={
            "interactive_clients": {
                "claude_code": False,
                "codex": True,
                "claude_desktop": False,
            },
            "default_terminal_client": "codex",
            "automation_enabled": False,
            "automation_backend": "none",
        },
    )

    assert result["enabled_clients"] == ["codex"]
    assert result["clients"]["claude_code"]["reason"] == "disabled in client preferences"
    assert result["clients"]["claude_desktop"]["reason"] == "disabled in client preferences"
    assert result["clients"]["codex"]["skipped"] is True


def test_sync_claude_bootstrap_preserves_user_block(tmp_path):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"
    bootstrap_path = home / ".claude" / "CLAUDE.md"
    bootstrap_path.parent.mkdir(parents=True)
    bootstrap_path.write_text(
        "<!-- nexo-claude-md-version: 1.0.0 -->\n"
        "******CORE******\n"
        "<!-- nexo:core:start -->\nold core\n<!-- nexo:core:end -->\n\n"
        "******USER******\n"
        "<!-- nexo:user:start -->\nSPECIAL USER RULE\n<!-- nexo:user:end -->\n"
    )

    result = client_sync.sync_claude_code(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    updated = bootstrap_path.read_text()
    assert "SPECIAL USER RULE" in updated
    assert "old core" not in updated
    assert "nexo-claude-md-version: 2.0.0" in updated


def test_sync_claude_bootstrap_migrates_legacy_file_into_core_user_contract(tmp_path):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"
    bootstrap_path = home / ".claude" / "CLAUDE.md"
    bootstrap_path.parent.mkdir(parents=True)
    bootstrap_path.write_text(
        "<!-- nexo-claude-md-version: 1.0.0 -->\n"
        "# Atlas — Cognitive Co-Operator\n"
        "I am Atlas, a cognitive co-operator powered by NEXO Brain.\n"
        "<!-- nexo:start:startup -->\nlegacy startup\n<!-- nexo:end:startup -->\n"
        "Operator note: remember the private QA machine.\n"
    )

    result = client_sync.sync_claude_code(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    updated = bootstrap_path.read_text()
    assert "******CORE******" in updated
    assert "******USER******" in updated
    assert "Operator note: remember the private QA machine." in updated


def test_bootstrap_docs_resolve_templates_for_runtime_layout(tmp_path):
    import bootstrap_docs

    runtime_root = tmp_path / "runtime"
    (runtime_root / "templates").mkdir(parents=True)
    module_file = runtime_root / "bootstrap_docs.py"
    module_file.write_text("# placeholder\n")

    resolved = bootstrap_docs._resolve_templates_dir(module_file)

    assert resolved == runtime_root / "templates"
