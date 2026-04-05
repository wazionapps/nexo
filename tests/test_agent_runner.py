import json
import os
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_build_interactive_client_command_uses_codex_when_selected(tmp_path, monkeypatch):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_codex_cli", lambda: "/tmp/fake-codex")
    monkeypatch.setattr(agent_runner, "_load_client_bootstrap_prompt", lambda client: "You are NEXO.")
    monkeypatch.setattr(agent_runner, "_codex_managed_initial_messages_enabled", lambda: False)

    client, cmd = agent_runner.build_interactive_client_command(
        target=tmp_path,
        preferences={
            "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
            "default_terminal_client": "codex",
            "automation_enabled": True,
            "automation_backend": "claude_code",
            "client_runtime_profiles": {
                "claude_code": {"model": "claude-opus-4-6[1m]", "reasoning_effort": ""},
                "codex": {"model": "gpt-5.4", "reasoning_effort": "xhigh"},
            },
        },
    )

    assert client == "codex"
    assert cmd[:2] == ["/tmp/fake-codex", "--full-auto"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert cmd[2:4] == ["-c", 'initial_messages=[{role="system",content="You are NEXO."}]']
    assert cmd[4:6] == ["-m", "gpt-5.4"]
    assert cmd[6:8] == ["-c", 'model_reasoning_effort="xhigh"']
    assert cmd[-2:] == ["-C", str(tmp_path)]


def test_build_interactive_client_command_preserves_claude_flags(tmp_path, monkeypatch):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_claude_cli", lambda: "/tmp/fake-claude")

    client, cmd = agent_runner.build_interactive_client_command(
        target=tmp_path,
        preferences={
            "interactive_clients": {"claude_code": True, "codex": False, "claude_desktop": False},
            "default_terminal_client": "claude_code",
            "automation_enabled": True,
            "automation_backend": "claude_code",
            "client_runtime_profiles": {
                "claude_code": {"model": "claude-opus-4-6[1m]", "reasoning_effort": ""},
                "codex": {"model": "gpt-5.4", "reasoning_effort": "xhigh"},
            },
        },
    )

    assert client == "claude_code"
    assert cmd == ["/tmp/fake-claude", "--model", "claude-opus-4-6[1m]", "--dangerously-skip-permissions", str(tmp_path)]


def test_run_automation_prompt_uses_claude_backend_command(monkeypatch, tmp_path):
    import agent_runner

    captured = {}
    monkeypatch.setattr(agent_runner, "_resolve_claude_cli", lambda: "/tmp/fake-claude")
    monkeypatch.setattr(agent_runner, "load_client_preferences", lambda: {
        "interactive_clients": {"claude_code": True, "codex": False, "claude_desktop": False},
        "default_terminal_client": "claude_code",
        "automation_enabled": True,
        "automation_backend": "claude_code",
        "client_runtime_profiles": {
            "claude_code": {"model": "claude-opus-4-6[1m]", "reasoning_effort": ""},
            "codex": {"model": "gpt-5.4", "reasoning_effort": "xhigh"},
        },
    })

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        captured["cwd"] = kwargs["cwd"]
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    result = agent_runner.run_automation_prompt(
        "Do the thing",
        cwd=tmp_path,
        model="opus",
        timeout=12,
        output_format="text",
        allowed_tools="Read,Write",
        append_system_prompt="JSON only",
    )

    assert result.returncode == 0
    assert captured["cmd"] == [
        "/tmp/fake-claude",
        "-p",
        "Do the thing",
        "--model",
        "claude-opus-4-6[1m]",
        "--output-format",
        "text",
        "--append-system-prompt",
        "JSON only",
        "--allowedTools",
        "Read,Write",
    ]
    assert captured["env"]["NEXO_HEADLESS"] == "1"
    assert captured["cwd"] == str(tmp_path.resolve())


def test_run_automation_prompt_uses_codex_exec_output_file(monkeypatch, tmp_path):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_codex_cli", lambda: "/tmp/fake-codex")
    monkeypatch.setattr(agent_runner, "_load_client_bootstrap_prompt", lambda client: "You are NEXO.")
    monkeypatch.setattr(agent_runner, "_codex_managed_initial_messages_enabled", lambda: False)
    monkeypatch.setattr(agent_runner, "load_client_preferences", lambda: {
        "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
        "default_terminal_client": "codex",
        "automation_enabled": True,
        "automation_backend": "codex",
        "client_runtime_profiles": {
            "claude_code": {"model": "claude-opus-4-6[1m]", "reasoning_effort": ""},
            "codex": {"model": "gpt-5.4", "reasoning_effort": "xhigh"},
        },
    })

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out_idx = cmd.index("-o") + 1
        output_path = cmd[out_idx]
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write("OK FROM CODEX")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    result = agent_runner.run_automation_prompt(
        "Summarize",
        cwd=tmp_path,
        model="opus",
        output_format="text",
        append_system_prompt="Return exactly one paragraph.",
        allowed_tools="Read,Grep",
    )

    assert result.returncode == 0
    assert result.stdout == "OK FROM CODEX"
    assert captured["cmd"][:6] == [
        "/tmp/fake-codex",
        "exec",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "--ephemeral",
        "-C",
    ]
    assert "-m" in captured["cmd"]
    model_idx = captured["cmd"].index("-m") + 1
    assert captured["cmd"][model_idx] == "gpt-5.4"
    config_values = [captured["cmd"][idx + 1] for idx, part in enumerate(captured["cmd"]) if part == "-c"]
    assert 'initial_messages=[{role="system",content="You are NEXO."}]' in config_values
    assert 'model_reasoning_effort="xhigh"' in config_values
    prompt = captured["cmd"][-1]
    assert "SYSTEM INSTRUCTIONS" in prompt
    assert "TOOLING SCOPE" in prompt
    assert "Summarize" in prompt


def test_probe_automation_backend_reports_disabled(monkeypatch):
    import agent_runner

    monkeypatch.setattr(agent_runner, "resolve_automation_backend", lambda preferences=None: "none")

    result = agent_runner.probe_automation_backend()

    assert result["ok"] is False
    assert result["backend"] == "none"


def test_codex_backend_maps_legacy_opus_hint_to_configured_profile(monkeypatch, tmp_path):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_codex_cli", lambda: "/tmp/fake-codex")
    monkeypatch.setattr(agent_runner, "_load_client_bootstrap_prompt", lambda client: "You are NEXO.")
    monkeypatch.setattr(agent_runner, "_codex_managed_initial_messages_enabled", lambda: False)
    monkeypatch.setattr(agent_runner, "load_client_preferences", lambda: {
        "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
        "default_terminal_client": "codex",
        "automation_enabled": True,
        "automation_backend": "codex",
        "client_runtime_profiles": {
            "claude_code": {"model": "claude-opus-4-6[1m]", "reasoning_effort": ""},
            "codex": {"model": "gpt-5.4-mini", "reasoning_effort": "high"},
        },
    })

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out_idx = cmd.index("-o") + 1
        with open(cmd[out_idx], "w", encoding="utf-8") as fh:
            fh.write("OK")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    agent_runner.run_automation_prompt(
        "Do it",
        cwd=tmp_path,
        model="opus",
        output_format="text",
    )

    assert captured["cmd"][captured["cmd"].index("-m") + 1] == "gpt-5.4-mini"
    config_values = [captured["cmd"][idx + 1] for idx, part in enumerate(captured["cmd"]) if part == "-c"]
    assert 'initial_messages=[{role="system",content="You are NEXO."}]' in config_values
    assert 'model_reasoning_effort="high"' in config_values


def test_codex_runner_skips_inline_bootstrap_when_global_bootstrap_is_managed(monkeypatch, tmp_path):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_codex_cli", lambda: "/tmp/fake-codex")
    monkeypatch.setattr(agent_runner, "_load_client_bootstrap_prompt", lambda client: "You are NEXO.")
    monkeypatch.setattr(agent_runner, "_codex_managed_initial_messages_enabled", lambda: True)

    client, cmd = agent_runner.build_interactive_client_command(
        target=tmp_path,
        preferences={
            "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
            "default_terminal_client": "codex",
            "automation_enabled": True,
            "automation_backend": "codex",
            "client_runtime_profiles": {
                "claude_code": {"model": "claude-opus-4-6[1m]", "reasoning_effort": ""},
                "codex": {"model": "gpt-5.4", "reasoning_effort": "xhigh"},
            },
        },
    )

    assert client == "codex"
    assert cmd[:2] == ["/tmp/fake-codex", "--full-auto"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    config_values = [cmd[idx + 1] for idx, part in enumerate(cmd) if part == "-c"]
    assert not any("initial_messages=" in value for value in config_values)
    assert 'model_reasoning_effort="xhigh"' in config_values


def test_build_followup_terminal_shell_command_uses_codex_full_auto_only(monkeypatch, tmp_path):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_codex_cli", lambda: "/tmp/fake-codex")
    monkeypatch.setattr(agent_runner, "_load_client_bootstrap_prompt", lambda client: "You are NEXO.")
    monkeypatch.setattr(agent_runner, "_codex_managed_initial_messages_enabled", lambda: False)

    client, command = agent_runner.build_followup_terminal_shell_command(
        "/tmp/followup.txt",
        client="codex",
        cwd=tmp_path,
        preferences={
            "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
            "default_terminal_client": "codex",
            "automation_enabled": True,
            "automation_backend": "codex",
            "client_runtime_profiles": {
                "claude_code": {"model": "claude-opus-4-6[1m]", "reasoning_effort": ""},
                "codex": {"model": "gpt-5.4", "reasoning_effort": "xhigh"},
            },
        },
    )

    assert client == "codex"
    parsed = shlex.split(command)
    assert parsed[:2] == ["/tmp/fake-codex", "--full-auto"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in parsed
    assert parsed[-1] == "NEXO: execute followup from file $(cat /tmp/followup.txt)"
