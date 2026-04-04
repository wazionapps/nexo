import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_build_interactive_client_command_uses_codex_when_selected(tmp_path, monkeypatch):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_codex_cli", lambda: "/tmp/fake-codex")

    client, cmd = agent_runner.build_interactive_client_command(
        target=tmp_path,
        preferences={
            "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
            "default_terminal_client": "codex",
            "automation_enabled": True,
            "automation_backend": "claude_code",
        },
    )

    assert client == "codex"
    assert cmd == ["/tmp/fake-codex", "-C", str(tmp_path)]


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
        },
    )

    assert client == "claude_code"
    assert cmd == ["/tmp/fake-claude", "--dangerously-skip-permissions", str(tmp_path)]


def test_run_automation_prompt_uses_claude_backend_command(monkeypatch, tmp_path):
    import agent_runner

    captured = {}
    monkeypatch.setattr(agent_runner, "_resolve_claude_cli", lambda: "/tmp/fake-claude")
    monkeypatch.setattr(agent_runner, "load_client_preferences", lambda: {
        "interactive_clients": {"claude_code": True, "codex": False, "claude_desktop": False},
        "default_terminal_client": "claude_code",
        "automation_enabled": True,
        "automation_backend": "claude_code",
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
        "opus",
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
    monkeypatch.setattr(agent_runner, "load_client_preferences", lambda: {
        "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
        "default_terminal_client": "codex",
        "automation_enabled": True,
        "automation_backend": "codex",
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
    assert "-m" not in captured["cmd"]
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
