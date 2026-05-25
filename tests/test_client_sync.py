"""Tests for shared client sync across Claude Code, Claude Desktop, and Codex."""

import json
import os
import subprocess
import sys
from pathlib import Path


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_bootstrap_docs_imports_without_nexo_home(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    env.pop("NEXO_HOME", None)
    env["PYTHONPATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))

    result = subprocess.run(
        [sys.executable, "-c", "import bootstrap_docs; print(bootstrap_docs.TEMPLATES_DIR)"],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 0, result.stderr


def _make_runtime(root: Path, *, operator_name: str = "Atlas") -> Path:
    runtime = root / "runtime"
    (runtime / ".venv" / "bin").mkdir(parents=True)
    (runtime / ".venv" / "bin" / "python3").write_text("")
    (runtime / "server.py").write_text("print('server')\n")
    hooks_dir = runtime / "hooks"
    hooks_dir.mkdir(parents=True)
    manifest = {
        "version": "1.0",
        "hooks": [
            {"event": "SessionStart", "handler": "src/hooks/session_start.py", "critical": True},
            {"event": "UserPromptSubmit", "handler": "src/hooks/auto_capture.py", "critical": False},
            {"event": "PreToolUse", "handler": "src/hooks/pre_tool_use.py", "critical": True},
            {"event": "PostToolUse", "handler": "src/hooks/post_tool_use.py", "critical": False},
            {"event": "PreCompact", "handler": "src/hooks/pre_compact.py", "critical": True},
            {"event": "Stop", "handler": "src/hooks/stop.py", "critical": True},
            {"event": "Notification", "handler": "src/hooks/notification.py", "critical": False},
            {"event": "SubagentStop", "handler": "src/hooks/subagent_stop.py", "critical": False},
        ],
    }
    (hooks_dir / "manifest.json").write_text(json.dumps(manifest))
    for handler in (
        "session_start.py",
        "auto_capture.py",
        "pre_tool_use.py",
        "post_tool_use.py",
        "pre_compact.py",
        "stop.py",
        "notification.py",
        "subagent_stop.py",
    ):
        (hooks_dir / handler).write_text("#!/usr/bin/env python3\n")
    for script in (
        "daily-briefing-check.sh",
        "session-start.sh",
        "session-stop.sh",
        "protocol-pretool-guardrail.sh",
        "heartbeat-user-msg.sh",
        "capture-tool-logs.sh",
        "capture-session.sh",
        "inbox-hook.sh",
        "protocol-guardrail.sh",
        "heartbeat-posttool.sh",
        "pre-compact.sh",
        "post-compact.sh",
        "heartbeat-enforcement.py",
    ):
        (hooks_dir / script).write_text("#!/bin/bash\n")
    payload = {}
    if operator_name is not None:
        payload["operator_name"] = operator_name
    (runtime / "version.json").write_text(json.dumps(payload))
    return runtime


def _normalize_home(path: Path, home: Path) -> str:
    try:
        return "~/" + path.resolve().relative_to(home.resolve()).as_posix()
    except Exception:
        return str(path)


def test_sync_claude_code_preserves_existing_settings(tmp_path):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    mcp_path = home / ".claude.json"
    cortex_path = home / ".claude" / "mcp-cortex.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "mcpServers": {"other": {"command": "node", "args": ["other.js"]}},
        "hooks": {"SessionStart": [{"matcher": "*", "hooks": []}]},
    }))
    mcp_path.write_text(json.dumps({
        "mcpServers": {"legacy-root": {"command": "node", "args": ["root.js"]}},
        "theme": "dark",
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
    all_hook_commands = [
        hook["command"]
        for sections in payload["hooks"].values()
        for section in sections
        for hook in section.get("hooks", [])
    ]
    assert any("session_start.py" in command for command in all_hook_commands)
    assert any("auto_capture.py" in command for command in all_hook_commands)
    assert any("pre_tool_use.py" in command for command in all_hook_commands)
    assert any("post_tool_use.py" in command for command in all_hook_commands)
    assert any("pre_compact.py" in command for command in all_hook_commands)
    assert any("stop.py" in command for command in all_hook_commands)
    assert any("notification.py" in command for command in all_hook_commands)
    assert any("subagent_stop.py" in command for command in all_hook_commands)
    assert not any("heartbeat-user-msg.sh" in command for command in all_hook_commands)
    assert not any("protocol-pretool-guardrail.sh" in command for command in all_hook_commands)
    assert payload["mcpServers"]["other"]["command"] == "node"
    assert payload["mcpServers"]["nexo"]["args"] == [str(runtime / "server.py")]
    assert payload["mcpServers"]["nexo"]["env"]["NEXO_HOME"] == str(runtime)
    assert payload["mcpServers"]["nexo"]["env"]["NEXO_CODE"] == str(runtime)
    assert payload["mcpServers"]["nexo"]["env"]["NEXO_NAME"] == "Atlas"
    assert payload["mcpServers"]["nexo"]["env"]["NEXO_MCP_CLIENT"] == "claude_code"
    mcp_payload = json.loads(mcp_path.read_text())
    assert mcp_payload["theme"] == "dark"
    assert mcp_payload["mcpServers"]["legacy-root"]["args"] == ["root.js"]
    assert mcp_payload["mcpServers"]["nexo"]["args"] == [str(runtime / "server.py")]
    assert mcp_payload["mcpServers"]["nexo"]["env"]["NEXO_MCP_CLIENT"] == "claude_code"
    cortex_payload = json.loads(cortex_path.read_text())
    assert cortex_payload["mcpServers"]["nexo"]["args"] == [str(runtime / "server.py")]
    assert cortex_payload["mcpServers"]["nexo"]["env"]["NEXO_MCP_CLIENT"] == "claude_code"
    assert result["mcp_path"] == str(mcp_path)
    assert result["cortex_mcp_path"] == str(cortex_path)
    bootstrap_path = home / ".claude" / "CLAUDE.md"
    assert bootstrap_path.is_file()
    bootstrap_text = bootstrap_path.read_text()
    assert "******CORE******" in bootstrap_text
    assert "******USER******" in bootstrap_text
    assert "Evolution" in bootstrap_text


def test_resolve_operator_name_falls_back_to_neutral_default(tmp_path):
    import client_sync

    runtime = tmp_path / "runtime"
    (runtime / "personal" / "brain").mkdir(parents=True, exist_ok=True)
    (runtime / "version.json").write_text(json.dumps({"operator_name": ""}))

    assert client_sync._resolve_operator_name(runtime) == "Nova"


def test_build_server_config_normalizes_versioned_runtime_paths_to_managed_core(tmp_path):
    import client_sync

    nexo_home = tmp_path / ".nexo"
    core = nexo_home / "core"
    versioned = core / "versions" / "7.9.6"
    (nexo_home / ".venv" / "bin").mkdir(parents=True)
    (nexo_home / ".venv" / "bin" / "python3").write_text("")
    (core / "server.py").parent.mkdir(parents=True, exist_ok=True)
    (core / "server.py").write_text("print('core')\n")
    (versioned / "server.py").parent.mkdir(parents=True, exist_ok=True)
    (versioned / "server.py").write_text("print('versioned')\n")

    config = client_sync.build_server_config(
        nexo_home=nexo_home,
        runtime_root=versioned,
        operator_name="Atlas",
        client="claude_code",
    )

    assert config["args"] == [str(core / "server.py")]
    assert config["env"]["NEXO_CODE"] == str(core)
    assert config["env"]["NEXO_MCP_CLIENT"] == "claude_code"


def test_sync_claude_code_removes_legacy_managed_hooks_but_keeps_custom_hooks(tmp_path):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "NEXO_HOME=/Users/franciscoc/claude bash /Users/franciscoc/claude/hooks/heartbeat-guard.sh",
                            "timeout": 5,
                        },
                        {
                            "type": "command",
                            "command": "bash /tmp/custom-post-tool.sh",
                            "timeout": 9,
                        },
                    ],
                }
            ]
        }
    }))

    result = client_sync.sync_claude_code(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    payload = json.loads(settings_path.read_text())
    post_tool_hooks = payload["hooks"]["PostToolUse"][0]["hooks"]
    commands = [hook["command"] for hook in post_tool_hooks]
    assert not any("heartbeat-guard.sh" in command for command in commands)
    assert any("custom-post-tool.sh" in command for command in commands)
    assert any("post_tool_use.py" in command for command in commands)
    assert not any("capture-tool-logs.sh" in command for command in commands)
    assert not any("protocol-guardrail.sh" in command for command in commands)


def test_sync_claude_code_rewrites_legacy_shell_hook_paths_to_unified_manifest_hooks(tmp_path):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "hooks": {
            "UserPromptSubmit": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "NEXO_HOME=/Users/franciscoc/.nexo bash /Users/franciscoc/.nexo/scripts/heartbeat-user-msg.sh",
                            "timeout": 3,
                        }
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "NEXO_HOME=/Users/franciscoc/.nexo bash /Users/franciscoc/.nexo/scripts/heartbeat-posttool.sh",
                            "timeout": 3,
                        }
                    ],
                }
            ],
        }
    }))

    result = client_sync.sync_claude_code(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    payload = json.loads(settings_path.read_text())
    user_prompt_hooks = payload["hooks"]["UserPromptSubmit"][0]["hooks"]
    post_tool_hooks = payload["hooks"]["PostToolUse"][0]["hooks"]
    user_prompt_commands = [hook["command"] for hook in user_prompt_hooks]
    post_tool_commands = [hook["command"] for hook in post_tool_hooks]
    assert any("hooks/auto_capture.py" in command for command in user_prompt_commands)
    assert not any("scripts/heartbeat-user-msg.sh" in command for command in user_prompt_commands)
    assert not any("hooks/heartbeat-user-msg.sh" in command for command in user_prompt_commands)
    assert any("hooks/post_tool_use.py" in command for command in post_tool_commands)
    assert not any("scripts/heartbeat-posttool.sh" in command for command in post_tool_commands)
    assert not any("hooks/heartbeat-posttool.sh" in command for command in post_tool_commands)


def test_sync_all_clients_writes_guardian_runtime_surfaces_snapshot(tmp_path):
    import client_sync

    runtime = _make_runtime(tmp_path)
    preset_dir = runtime / "personal" / "brain" / "presets"
    preset_dir.mkdir(parents=True, exist_ok=True)
    (preset_dir / "entities_universal.json").write_text(json.dumps({
        "entities": [
            {
                "type": "host",
                "name": "maria",
                "metadata": {"aliases": ["maria-db"], "access_mode": "read_only"},
            }
        ]
    }))

    result = client_sync.sync_all_clients(
        nexo_home=runtime,
        runtime_root=runtime,
        enabled_clients=["claude_desktop"],
        user_home=tmp_path / "home",
    )

    assert result["ok"] is True
    surfaces = result["guardian_runtime_surfaces"]
    assert surfaces["ok"] is True
    assert surfaces["entity_count"] == 1
    written = json.loads((runtime / "personal" / "brain" / "guardian-runtime-surfaces.json").read_text())
    assert written["source"] == "preset_fallback"
    assert "maria" in written["known_hosts"]


def test_sync_claude_code_prunes_legacy_python_core_hooks_from_temp_runtime(tmp_path):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    temp_home = (
        "/private/var/folders/_1/kdg9j8n50sx0w88mmbhdbqqr0000gn/T/"
        "pytest-of-franciscoc/pytest-94/test_skip_flag_produces_expect0/nexo-home"
    )
    settings_path.write_text(json.dumps({
        "hooks": {
            "SessionStart": [{
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"NEXO_HOME={temp_home} python3 {temp_home}/hooks/session_start.py", "timeout": 40}],
            }],
            "Stop": [{
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"NEXO_HOME={temp_home} python3 {temp_home}/hooks/stop.py", "timeout": 15}],
            }],
            "UserPromptSubmit": [{
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"NEXO_HOME={temp_home} python3 {temp_home}/hooks/auto_capture.py", "timeout": 5}],
            }],
            "PostToolUse": [{
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"NEXO_HOME={temp_home} python3 {temp_home}/hooks/post_tool_use.py", "timeout": 20}],
            }],
            "PreCompact": [{
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"NEXO_HOME={temp_home} python3 {temp_home}/hooks/pre_compact.py", "timeout": 15}],
            }],
            "Notification": [{
                "matcher": "*",
                "hooks": [
                    {"type": "command", "command": f"NEXO_HOME={temp_home} python3 {temp_home}/hooks/notification.py", "timeout": 3},
                    {"type": "command", "command": "python3 /tmp/custom-notification.py", "timeout": 3},
                ],
            }],
            "SubagentStop": [{
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"NEXO_HOME={temp_home} python3 {temp_home}/hooks/subagent_stop.py", "timeout": 10}],
            }],
        }
    }))

    result = client_sync.sync_claude_code(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    payload = json.loads(settings_path.read_text())
    commands = [
        hook["command"]
        for sections in payload["hooks"].values()
        for section in sections
        for hook in section["hooks"]
    ]
    assert not any(temp_home in command for command in commands)
    assert any("custom-notification.py" in command for command in commands)
    assert any("session_start.py" in command for command in commands)
    assert any("auto_capture.py" in command for command in commands)
    assert any("post_tool_use.py" in command for command in commands)


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
    assert payload["mcpServers"]["nexo"]["env"]["NEXO_MCP_CLIENT"] == "claude_desktop"
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
    assert "NEXO_MCP_CLIENT=codex" in captured["cmd"]
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
    assert 'model = "gpt-5.5"' in config_text
    assert 'model_reasoning_effort = "xhigh"' in config_text
    assert "initial_messages" not in config_text
    assert "[nexo.codex]" in config_text
    assert "bootstrap_managed = true" in config_text
    assert "mcp_managed = true" in config_text
    assert 'approval_policy = "never"' in config_text
    assert 'sandbox_mode = "danger-full-access"' in config_text
    assert "[features]" in config_text
    assert "hooks = true" in config_text
    assert "codex_hooks" not in config_text
    assert "[mcp_servers.nexo]" in config_text
    assert 'NEXO_MCP_CLIENT = "codex"' in config_text
    hooks_path = home / ".codex" / "hooks.json"
    hooks_payload = json.loads(hooks_path.read_text())
    pretool = hooks_payload["hooks"]["PreToolUse"]
    assert pretool[0]["matcher"] == client_sync.CODEX_PRETOOL_MATCHER
    pretool_commands = [
        hook["command"]
        for section in pretool
        for hook in section.get("hooks", [])
    ]
    assert any("pre_tool_use.py" in command for command in pretool_commands)
    assert result["hooks"]["managed_hook_count"] == 1


def test_codex_hook_command_supports_native_windows_cmd(tmp_path):
    import client_sync

    runtime = _make_runtime(tmp_path)
    command = client_sync._render_hook_command(
        {
            "event": "PreToolUse",
            "handler": "pre_tool_use.py",
            "interpreter": "python",
        },
        nexo_home=runtime,
        runtime_root=runtime,
        hooks_dir=runtime / "hooks",
        windows_shell=True,
    )

    assert command.startswith(f'set "NEXO_HOME={runtime}" && set "NEXO_CODE={runtime}" && ')
    assert " NEXO_HOME=" not in command
    assert "pre_tool_use.py" in command
    assert str(runtime / ".venv" / "bin" / "python3") in command


def test_sync_codex_preserves_explicit_approval_and_sandbox(tmp_path):
    import client_sync

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'approval_policy = "on-request"\n'
        'sandbox_mode = "workspace-write"\n',
        encoding="utf-8",
    )

    result = client_sync._sync_codex_managed_config(
        config_path,
        bootstrap_prompt="",
        runtime_profile={"model": "gpt-5.5", "reasoning_effort": "low"},
        server_config={},
    )

    assert result["ok"] is True
    config_text = config_path.read_text(encoding="utf-8")
    assert 'approval_policy = "on-request"' in config_text
    assert 'sandbox_mode = "workspace-write"' in config_text
    assert 'model = "gpt-5.5"' in config_text
    assert "hooks = true" in config_text
    assert "codex_hooks" not in config_text


def test_sync_codex_managed_config_migrates_deprecated_codex_hooks_flag(tmp_path):
    import client_sync

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[features]\n"
        "codex_hooks = true\n",
        encoding="utf-8",
    )

    result = client_sync._sync_codex_managed_config(
        config_path,
        bootstrap_prompt="",
        runtime_profile={},
        server_config={},
    )

    assert result["ok"] is True
    config_text = config_path.read_text(encoding="utf-8")
    assert "hooks = true" in config_text
    assert "codex_hooks" not in config_text


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
    assert "pre_tool_use.py" in (home / ".codex" / "hooks.json").read_text()


def test_sync_codex_desktop_managed_ignores_global_codex_without_vendor(tmp_path, monkeypatch):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"

    monkeypatch.setenv("NEXO_DESKTOP_MANAGED", "1")
    monkeypatch.setattr(client_sync.shutil, "which", lambda name: "/tmp/global-codex" if name == "codex" else None)
    monkeypatch.setattr(
        client_sync,
        "detect_installed_clients",
        lambda user_home=None: {
            "claude_code": {"installed": False, "path": "", "detected_by": "missing"},
            "codex": {"installed": False, "path": "", "detected_by": "missing"},
            "claude_desktop": {"installed": False, "path": "", "detected_by": "missing"},
        },
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Desktop-managed Codex sync must not execute a global PATH codex")

    monkeypatch.setattr(client_sync.subprocess, "run", fail_if_called)

    result = client_sync.sync_codex(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    assert result["skipped"] is True
    assert "codex binary not found" in result["reason"]
    assert (home / ".codex" / "AGENTS.md").is_file()
    assert "[mcp_servers.nexo]" in (home / ".codex" / "config.toml").read_text()


def test_sync_all_clients_auto_installs_selected_claude_code_when_missing(tmp_path, monkeypatch):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"
    attempts = []
    install_state = {"installed": False}

    def fake_detect(user_home=None):
        return {
            "claude_code": {
                "installed": install_state["installed"],
                "path": "/tmp/fake-claude" if install_state["installed"] else "",
                "detected_by": "binary" if install_state["installed"] else "missing",
            },
            "codex": {"installed": False, "path": "", "detected_by": "missing"},
            "claude_desktop": {"installed": False, "path": "", "detected_by": "missing"},
        }

    def fake_run(cmd, **kwargs):
        attempts.append(cmd)
        if cmd[:2] == ["npm", "install"]:
            install_state["installed"] = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(client_sync, "detect_installed_clients", fake_detect)
    monkeypatch.setattr(client_sync.subprocess, "run", fake_run)
    monkeypatch.setattr(client_sync.sys, "platform", "darwin")

    result = client_sync.sync_all_clients(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
        preferences={
            "interactive_clients": {
                "claude_code": True,
                "codex": False,
                "claude_desktop": False,
            },
            "default_terminal_client": "claude_code",
            "automation_enabled": True,
            "automation_backend": "claude_code",
        },
        auto_install_missing_claude=True,
    )

    assert result["ok"] is True
    assert attempts[0] == ["npx", "-y", "@anthropic-ai/claude-code", "--version"]
    assert attempts[1] == ["npm", "install", "-g", "@anthropic-ai/claude-code"]
    assert result["install_results"]["claude_code"]["action"] == "installed"
    assert result["install_results"]["claude_code"]["path"] == "/tmp/fake-claude"


def test_sync_all_clients_auto_installs_claude_via_bundled_npm_runtime(tmp_path, monkeypatch):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"
    desktop_node = tmp_path / "electron-node"
    npm_cli = tmp_path / "npm-cli.js"
    desktop_node.write_text("")
    npm_cli.write_text("")

    attempts = []
    install_state = {"installed": False}

    def fake_detect(user_home=None):
        managed_path = Path(user_home or home) / ".nexo" / "runtime" / "bootstrap" / "npm-global" / "bin" / "claude"
        return {
            "claude_code": {
                "installed": install_state["installed"],
                "path": str(managed_path) if install_state["installed"] else "",
                "detected_by": "binary" if install_state["installed"] else "missing",
            },
            "codex": {"installed": False, "path": "", "detected_by": "missing"},
            "claude_desktop": {"installed": False, "path": "", "detected_by": "missing"},
        }

    def fake_run(cmd, **kwargs):
        attempts.append({"cmd": cmd, "env": kwargs.get("env", {})})
        if cmd[:4] == [str(desktop_node), str(npm_cli), "install", "-g"]:
            install_state["installed"] = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(client_sync, "detect_installed_clients", fake_detect)
    monkeypatch.setattr(client_sync.subprocess, "run", fake_run)
    monkeypatch.setenv("NEXO_DESKTOP_NODE", str(desktop_node))
    monkeypatch.setenv("NEXO_DESKTOP_NPM_CLI", str(npm_cli))

    result = client_sync.sync_all_clients(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
        preferences={
            "interactive_clients": {
                "claude_code": True,
                "codex": False,
                "claude_desktop": False,
            },
            "default_terminal_client": "claude_code",
            "automation_enabled": True,
            "automation_backend": "claude_code",
        },
        auto_install_missing_claude=True,
    )

    managed_prefix = home / ".nexo" / "runtime" / "bootstrap" / "npm-global"
    assert result["ok"] is True
    assert attempts[0]["cmd"] == [
        str(desktop_node),
        str(npm_cli),
        "install",
        "-g",
        "--prefix",
        str(managed_prefix),
        "@anthropic-ai/claude-code",
    ]
    assert attempts[0]["env"]["ELECTRON_RUN_AS_NODE"] == "1"
    assert result["install_results"]["claude_code"]["action"] == "installed_via_bundled_npm"
    assert (home / ".nexo" / "config" / "claude-cli-path").read_text().strip() == str(managed_prefix / "bin" / "claude")


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
    assert "NEXO_NAME=Nova" in captured["cmd"]
    bootstrap_text = (home / ".codex" / "AGENTS.md").read_text()
    assert "You are Nova" in bootstrap_text
    assert "You are NEXO" not in bootstrap_text


def test_sync_codex_prefers_calibration_assistant_name_over_blank_version_value(tmp_path, monkeypatch):
    import client_sync

    runtime = _make_runtime(tmp_path, operator_name="")
    home = tmp_path / "home"
    captured = {}

    calibration_dir = runtime / "personal" / "brain"
    calibration_dir.mkdir(parents=True, exist_ok=True)
    (calibration_dir / "calibration.json").write_text(json.dumps({
        "user": {
            "assistant_name": "Nero",
            "name": "Francisco",
            "language": "es",
        }
    }))

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
    assert "NEXO_NAME=Nero" in captured["cmd"]
    bootstrap_text = (home / ".codex" / "AGENTS.md").read_text()
    assert "You are Nero" in bootstrap_text


def test_ensure_claude_code_installed_desktop_managed_does_not_fallback_to_npx_or_global(monkeypatch, tmp_path):
    import client_sync

    home = tmp_path / "home"
    desktop_node = tmp_path / "electron-node"
    npm_cli = tmp_path / "npm-cli.js"
    desktop_node.write_text("")
    npm_cli.write_text("")
    attempts = []

    monkeypatch.setenv("NEXO_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("NEXO_DESKTOP_NODE", str(desktop_node))
    monkeypatch.setenv("NEXO_DESKTOP_NPM_CLI", str(npm_cli))
    monkeypatch.setattr(
        client_sync,
        "detect_installed_clients",
        lambda user_home=None: {
            "claude_code": {"installed": False, "path": "", "detected_by": "missing"},
            "codex": {"installed": False, "path": "", "detected_by": "missing"},
            "claude_desktop": {"installed": False, "path": "", "detected_by": "missing"},
        },
    )

    def fake_run(cmd, **kwargs):
        attempts.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "install failed")

    monkeypatch.setattr(client_sync.subprocess, "run", fake_run)

    result = client_sync.ensure_claude_code_installed(user_home=home)

    assert result["ok"] is False
    assert result["action"] == "managed_install_failed"
    assert attempts == [[
        str(desktop_node),
        str(npm_cli),
        "install",
        "-g",
        "--prefix",
        str(home / ".nexo" / "runtime" / "bootstrap" / "npm-global"),
        "@anthropic-ai/claude-code",
    ]]
    assert "npx" not in " ".join(" ".join(cmd) for cmd in attempts)
    assert "npm -g" not in result["error"]


def test_ensure_claude_code_installed_desktop_managed_requires_bundled_runtime(monkeypatch, tmp_path):
    import client_sync

    home = tmp_path / "home"

    monkeypatch.setenv("NEXO_DESKTOP_MANAGED", "1")
    monkeypatch.delenv("NEXO_DESKTOP_NODE", raising=False)
    monkeypatch.delenv("NEXO_DESKTOP_NPM_CLI", raising=False)
    monkeypatch.setattr(
        client_sync,
        "detect_installed_clients",
        lambda user_home=None: {
            "claude_code": {"installed": False, "path": "", "detected_by": "missing"},
            "codex": {"installed": False, "path": "", "detected_by": "missing"},
            "claude_desktop": {"installed": False, "path": "", "detected_by": "missing"},
        },
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("desktop-managed install should not call npx/npm-global fallbacks")

    monkeypatch.setattr(client_sync.subprocess, "run", fail_if_called)

    result = client_sync.ensure_claude_code_installed(user_home=home)

    assert result["ok"] is False
    assert result["action"] == "managed_install_failed"
    assert "bundled Claude runtime" in result["error"]


def test_ensure_codex_installed_desktop_managed_does_not_call_host_npm(monkeypatch, tmp_path):
    import client_sync

    home = tmp_path / "home"

    monkeypatch.setenv("NEXO_DESKTOP_MANAGED", "1")
    monkeypatch.delenv("NEXO_DESKTOP_NODE", raising=False)
    monkeypatch.delenv("NEXO_DESKTOP_NPM_CLI", raising=False)
    monkeypatch.setattr(client_sync, "_brain_bundle_root", lambda: tmp_path)
    monkeypatch.setattr(
        client_sync,
        "detect_installed_clients",
        lambda user_home=None: {
            "claude_code": {"installed": False, "path": "", "detected_by": "missing"},
            "codex": {"installed": False, "path": "", "detected_by": "missing"},
            "claude_desktop": {"installed": False, "path": "", "detected_by": "missing"},
        },
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("desktop-managed Codex install should not call host npm")

    monkeypatch.setattr(client_sync.subprocess, "run", fail_if_called)

    result = client_sync.ensure_codex_installed(user_home=home)

    assert result["ok"] is False
    assert result["action"] == "failed"
    assert "global `npm -g` fallbacks are disabled" in result["error"]


def test_codex_vendor_present_accepts_nested_optional_native_package(tmp_path):
    import client_sync

    managed_prefix = tmp_path / "npm-global"
    vendor_bin = (
        managed_prefix
        / "lib"
        / "node_modules"
        / "@openai"
        / "codex"
        / "node_modules"
        / "@openai"
        / "codex-darwin-arm64"
        / "vendor"
        / "aarch64-apple-darwin"
        / "bin"
        / "codex"
    )
    vendor_bin.parent.mkdir(parents=True, exist_ok=True)
    vendor_bin.write_text("#!/bin/sh\n")

    assert client_sync._codex_vendor_present(managed_prefix) is True


def test_codex_bundle_dir_can_come_from_desktop_env(monkeypatch, tmp_path):
    import client_sync

    bundled_codex = tmp_path / "Resources" / "brain-bundle" / "codex"
    bundled_codex.mkdir(parents=True)
    monkeypatch.setenv("NEXO_CODEX_BUNDLE_DIR", str(bundled_codex))

    assert client_sync._codex_bundle_dir() == bundled_codex


def test_codex_bundled_install_uses_wrapper_before_native_tarball(monkeypatch, tmp_path):
    import client_sync

    bundle = tmp_path / "codex"
    bundle.mkdir()
    wrapper = bundle / "openai-codex-0.133.0.tgz"
    native = bundle / "openai-codex-0.133.0-darwin-arm64.tgz"
    native.write_text("native")
    wrapper.write_text("wrapper")
    attempts = []
    captured = {}

    monkeypatch.setattr(client_sync, "_platform_slug", lambda: "darwin-arm64")
    monkeypatch.setattr(client_sync, "_bundled_npm_runtime", lambda: ("", ""))

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(client_sync.subprocess, "run", fake_run)

    assert client_sync._install_npm_package_from_bundle(
        bundle_dir=bundle,
        wrapper_pattern=r"^openai-codex-\d+\.\d+\.\d+\.tgz$",
        package_name=client_sync.CODEX_NPM_PACKAGE,
        managed_prefix=tmp_path / "prefix",
        env={},
        attempts=attempts,
    ) is True

    tgz_args = [Path(item).name for item in captured["cmd"] if str(item).endswith(".tgz")]
    assert tgz_args == [wrapper.name, native.name]
    assert attempts == []


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
    assert "nexo-claude-md-version:" in updated


def test_sync_claude_bootstrap_migrates_legacy_home_references_in_user_block(tmp_path):
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
        "<!-- nexo:user:start -->\n"
        "cat ~/claude/operations/.watchdog-alert\n"
        f"policy={home / 'claude' / 'brain' / 'policies.md'}\n"
        "<!-- nexo:user:end -->\n"
    )

    result = client_sync.sync_claude_code(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    updated = bootstrap_path.read_text()
    assert "~/claude" not in updated
    assert str(home / "claude") not in updated
    assert f"cat {_normalize_home(runtime, home)}/operations/.watchdog-alert" in updated
    assert f"policy={runtime / 'brain' / 'policies.md'}" in updated


def test_sync_claude_bootstrap_migrates_legacy_home_references_when_legacy_home_is_symlink(tmp_path):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "claude").symlink_to(runtime, target_is_directory=True)
    bootstrap_path = home / ".claude" / "CLAUDE.md"
    bootstrap_path.parent.mkdir(parents=True)
    bootstrap_path.write_text(
        "<!-- nexo-claude-md-version: 1.0.0 -->\n"
        "******CORE******\n"
        "<!-- nexo:core:start -->\nold core\n<!-- nexo:core:end -->\n\n"
        "******USER******\n"
        "<!-- nexo:user:start -->\n"
        "cat ~/claude/operations/.watchdog-alert\n"
        "<!-- nexo:user:end -->\n"
    )

    result = client_sync.sync_claude_code(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    updated = bootstrap_path.read_text()
    assert "~/claude" not in updated
    assert f"cat {_normalize_home(runtime, home)}/operations/.watchdog-alert" in updated


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


def test_sync_codex_bootstrap_migrates_legacy_home_references_in_user_block(tmp_path, monkeypatch):
    import client_sync

    runtime = _make_runtime(tmp_path)
    home = tmp_path / "home"
    bootstrap_path = home / ".codex" / "AGENTS.md"
    bootstrap_path.parent.mkdir(parents=True)
    bootstrap_path.write_text(
        "<!-- nexo-codex-agents-version: 1.0.0 -->\n"
        "******CORE******\n"
        "<!-- nexo:core:start -->\nold core\n<!-- nexo:core:end -->\n\n"
        "******USER******\n"
        "<!-- nexo:user:start -->\n"
        "Atlas: ~/claude/brain/project-atlas.json\n"
        f"db={home / 'claude' / 'data' / 'nexo.db'}\n"
        "<!-- nexo:user:end -->\n"
    )
    monkeypatch.setattr(client_sync.shutil, "which", lambda name: None)

    result = client_sync.sync_codex(
        nexo_home=runtime,
        runtime_root=runtime,
        operator_name="Atlas",
        user_home=home,
    )

    assert result["ok"] is True
    updated = bootstrap_path.read_text()
    assert "~/claude" not in updated
    assert str(home / "claude") not in updated
    assert f"Atlas: {_normalize_home(runtime, home)}/brain/project-atlas.json" in updated
    assert f"db={runtime / 'data' / 'nexo.db'}" in updated


def test_bootstrap_docs_resolve_templates_for_runtime_layout(tmp_path):
    import bootstrap_docs

    runtime_root = tmp_path / "runtime"
    (runtime_root / "templates").mkdir(parents=True)
    module_file = runtime_root / "bootstrap_docs.py"
    module_file.write_text("# placeholder\n")

    resolved = bootstrap_docs._resolve_templates_dir(module_file)

    assert resolved == runtime_root / "templates"


def test_bootstrap_docs_resolve_templates_for_versioned_runtime_uses_nexo_home_templates(tmp_path, monkeypatch):
    import bootstrap_docs

    runtime_root = tmp_path / "nexo-home"
    module_file = runtime_root / "core" / "versions" / "5.3.7" / "bootstrap_docs.py"
    module_file.parent.mkdir(parents=True)
    module_file.write_text("# placeholder\n")
    (runtime_root / "templates").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(runtime_root))

    resolved = bootstrap_docs._resolve_templates_dir(module_file)

    assert resolved == runtime_root / "templates"
