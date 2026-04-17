import json
import os
import shlex
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture(autouse=True)
def _register_test_harness_caller():
    """run_automation_prompt requires caller= as of v5.10.0. Tests in this
    file all use caller='test/harness'; register it for the duration of
    the test at the lowest tier so we never accidentally train on a real
    workload tier from a fixture."""
    from resonance_map import register_system_caller, unregister_system_caller
    register_system_caller("test/harness", "maximo")
    try:
        yield
    finally:
        unregister_system_caller("test/harness")


@pytest.fixture(autouse=True)
def _mock_enforcement_engine(monkeypatch):
    """Bypass enforcement engine so tests use subprocess.run directly."""
    try:
        import enforcement_engine
        def _passthrough(cmd, prompt="", cwd="", env=None, timeout=300):
            return subprocess.run(cmd, cwd=cwd or None, capture_output=True,
                                  text=True, timeout=timeout, env=env)
        monkeypatch.setattr(enforcement_engine, "run_with_enforcement", _passthrough)
    except ImportError:
        pass



def _claude_json_result(result: str = "ok", *, cost: float = 0.01) -> str:
    return json.dumps(
        {
            "result": result,
            "total_cost_usd": cost,
            "usage": {
                "input_tokens": 11,
                "cache_read_input_tokens": 2,
                "output_tokens": 7,
            },
        }
    )


def _codex_json_usage(*, input_tokens: int = 100, cached_input_tokens: int = 20, output_tokens: int = 30) -> str:
    return "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": input_tokens,
                        "cached_input_tokens": cached_input_tokens,
                        "output_tokens": output_tokens,
                    },
                }
            ),
        ]
    )


def test_build_interactive_client_command_uses_codex_when_selected(tmp_path, monkeypatch):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_codex_cli", lambda: "/tmp/fake-codex")
    monkeypatch.setattr(agent_runner, "_load_client_bootstrap_prompt", lambda client: "You are NEXO.")
    monkeypatch.setattr(agent_runner, "_codex_managed_initial_messages_enabled", lambda: False)
    monkeypatch.setattr(agent_runner, "_interactive_startup_prompt", lambda client: "Start NEXO now.")

    client, cmd = agent_runner.build_interactive_client_command(
        target=tmp_path,
        preferences={
            "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
            "default_terminal_client": "codex",
            "automation_enabled": True,
            "automation_backend": "claude_code",
            "client_runtime_profiles": {
                "claude_code": {"model": "claude-opus-4-7[1m]", "reasoning_effort": "max"},
                "codex": {"model": "gpt-5.4", "reasoning_effort": "xhigh"},
            },
        },
    )

    assert client == "codex"
    assert cmd[:5] == [
        "/tmp/fake-codex",
        "--sandbox",
        "danger-full-access",
        "--ask-for-approval",
        "never",
    ]
    assert "--full-auto" not in cmd
    assert cmd[5:7] == ["-c", 'initial_messages=[{role="system",content="You are NEXO."}]']
    assert cmd[7:9] == ["-m", "gpt-5.4"]
    assert cmd[9:11] == ["-c", 'model_reasoning_effort="xhigh"']
    assert cmd[-3:] == ["-C", str(tmp_path), "Start NEXO now."]


def test_build_interactive_client_command_preserves_claude_flags(tmp_path, monkeypatch):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_claude_cli", lambda: "/tmp/fake-claude")
    monkeypatch.setattr(agent_runner, "_build_enforcement_system_prompt", lambda: "")
    monkeypatch.setattr(agent_runner, "_interactive_startup_prompt", lambda client: "Start NEXO now.")

    client, cmd = agent_runner.build_interactive_client_command(
        target=tmp_path,
        preferences={
            "interactive_clients": {"claude_code": True, "codex": False, "claude_desktop": False},
            "default_terminal_client": "claude_code",
            "automation_enabled": True,
            "automation_backend": "claude_code",
            "client_runtime_profiles": {
                "claude_code": {"model": "claude-opus-4-7[1m]", "reasoning_effort": "max"},
                "codex": {"model": "gpt-5.4", "reasoning_effort": "xhigh"},
            },
        },
    )

    assert client == "claude_code"
    assert cmd == [
        "/tmp/fake-claude",
        "--model",
        "claude-opus-4-7[1m]",
        "--effort",
        "max",
        "--dangerously-skip-permissions",
        "Start NEXO now.",
    ]


def test_run_automation_prompt_uses_claude_backend_command(monkeypatch, tmp_path):
    import agent_runner

    captured = {}
    monkeypatch.setattr(agent_runner, "_resolve_claude_cli", lambda: "/tmp/fake-claude")
    monkeypatch.setattr(agent_runner, "_build_enforcement_system_prompt", lambda: "")
    monkeypatch.setattr(agent_runner, "_record_automation_run", lambda **kwargs: (True, ""))
    monkeypatch.setattr(agent_runner, "_build_enforcement_system_prompt", lambda: "")
    monkeypatch.setattr(agent_runner, "load_client_preferences", lambda: {
        "interactive_clients": {"claude_code": True, "codex": False, "claude_desktop": False},
        "default_terminal_client": "claude_code",
        "automation_enabled": True,
        "automation_backend": "claude_code",
        "client_runtime_profiles": {
            "claude_code": {"model": "claude-opus-4-7[1m]", "reasoning_effort": "max"},
            "codex": {"model": "gpt-5.4", "reasoning_effort": "xhigh"},
        },
    })

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, _claude_json_result("ok"), "")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)
    try:
        import enforcement_engine
        monkeypatch.setattr(enforcement_engine, "run_with_enforcement",
            lambda cmd, prompt="", cwd="", env=None, timeout=300: fake_run(cmd, cwd=cwd, env=env))
    except ImportError:
        pass

    result = agent_runner.run_automation_prompt(
        "Do the thing",
        caller="test/harness",
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
        "--dangerously-skip-permissions",
        "--model",
        "claude-opus-4-7[1m]",
        "--effort",
        "max",
        "--output-format",
        "json",
        "--append-system-prompt",
        "JSON only",
        "--allowedTools",
        "Read,Write",
    ]
    assert captured["env"]["NEXO_HEADLESS"] == "1"
    assert captured["env"]["NEXO_AUTOMATION"] == "1"
    assert captured["cwd"] == str(tmp_path.resolve())


def test_run_automation_prompt_uses_codex_exec_output_file(monkeypatch, tmp_path):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_codex_cli", lambda: "/tmp/fake-codex")
    monkeypatch.setattr(agent_runner, "_load_client_bootstrap_prompt", lambda client: "You are NEXO.")
    monkeypatch.setattr(agent_runner, "_codex_managed_initial_messages_enabled", lambda: False)
    monkeypatch.setattr(agent_runner, "_record_automation_run", lambda **kwargs: (True, ""))
    monkeypatch.setattr(agent_runner, "load_client_preferences", lambda: {
        "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
        "default_terminal_client": "codex",
        "automation_enabled": True,
        "automation_backend": "codex",
        "client_runtime_profiles": {
            "claude_code": {"model": "claude-opus-4-7[1m]", "reasoning_effort": "max"},
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
        return subprocess.CompletedProcess(cmd, 0, _codex_json_usage(), "")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    result = agent_runner.run_automation_prompt(
        "Summarize",
        caller="test/harness",
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
        "--json",
    ]
    assert captured["cmd"][6:8] == [
        "-C",
        str(tmp_path.resolve()),
    ]
    assert "-m" in captured["cmd"]
    model_idx = captured["cmd"].index("-m") + 1
    assert captured["cmd"][model_idx] == "gpt-5.4"
    config_values = [captured["cmd"][idx + 1] for idx, part in enumerate(captured["cmd"]) if part == "-c"]
    assert 'initial_messages=[{role="system",content="You are NEXO."}]' in config_values
    assert 'model_reasoning_effort="xhigh"' in config_values
    prompt = captured["cmd"][-1]
    assert "NEXO PROTOCOL (MANDATORY)" in prompt
    assert "nexo_task_open" in prompt
    assert "conditioned learnings" in prompt
    assert "SYSTEM INSTRUCTIONS" in prompt
    assert "TOOLING SCOPE" in prompt
    assert "Summarize" in prompt
    assert captured["cmd"][-1] == prompt


def test_run_automation_prompt_marks_public_contribution_env(monkeypatch, tmp_path):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_claude_cli", lambda: "/tmp/fake-claude")
    monkeypatch.setattr(agent_runner, "_build_enforcement_system_prompt", lambda: "")
    monkeypatch.setattr(agent_runner, "_record_automation_run", lambda **kwargs: (True, ""))
    monkeypatch.setattr(agent_runner, "load_client_preferences", lambda: {
        "interactive_clients": {"claude_code": True, "codex": False, "claude_desktop": False},
        "default_terminal_client": "claude_code",
        "automation_enabled": True,
        "automation_backend": "claude_code",
        "client_runtime_profiles": {
            "claude_code": {"model": "claude-opus-4-7[1m]", "reasoning_effort": "max"},
            "codex": {"model": "gpt-5.4", "reasoning_effort": "xhigh"},
        },
    })

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, _claude_json_result("ok"), "")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    result = agent_runner.run_automation_prompt(
        "Public contribution path",
        caller="test/harness",
        cwd=tmp_path,
        env={"NEXO_PUBLIC_CONTRIBUTION": "1"},
        output_format="text",
    )

    assert result.returncode == 0
    assert captured["env"]["NEXO_AUTOMATION"] == "1"
    assert captured["env"]["NEXO_PUBLIC_CONTRIBUTION"] == "1"


def test_run_automation_prompt_uses_fast_task_profile_for_backend_and_reasoning(monkeypatch, tmp_path):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_claude_cli", lambda: "/tmp/fake-claude")
    monkeypatch.setattr(agent_runner, "_build_enforcement_system_prompt", lambda: "")
    monkeypatch.setattr(agent_runner, "_resolve_codex_cli", lambda: "/tmp/fake-codex")
    monkeypatch.setattr(agent_runner, "_load_client_bootstrap_prompt", lambda client: "You are NEXO.")
    monkeypatch.setattr(agent_runner, "_codex_managed_initial_messages_enabled", lambda: False)
    monkeypatch.setattr(agent_runner, "_record_automation_run", lambda **kwargs: (True, ""))
    monkeypatch.setattr(agent_runner, "load_client_preferences", lambda: {
        "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
        "default_terminal_client": "claude_code",
        "automation_enabled": True,
        "automation_backend": "claude_code",
        "client_runtime_profiles": {
            "claude_code": {"model": "claude-opus-4-7[1m]", "reasoning_effort": "max"},
            "codex": {"model": "gpt-5.4", "reasoning_effort": "high"},
        },
        "automation_task_profiles": {
            "default": {"backend": "", "model": "", "reasoning_effort": ""},
            "fast": {"backend": "codex", "model": "gpt-5.4-mini", "reasoning_effort": "medium"},
            "balanced": {"backend": "", "model": "", "reasoning_effort": ""},
            "deep": {"backend": "claude_code", "model": "claude-opus-4-7[1m]", "reasoning_effort": "max"},
        },
    })

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out_idx = cmd.index("-o") + 1
        with open(cmd[out_idx], "w", encoding="utf-8") as fh:
            fh.write("FAST OK")
        return subprocess.CompletedProcess(cmd, 0, _codex_json_usage(input_tokens=20, cached_input_tokens=5, output_tokens=10), "")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    result = agent_runner.run_automation_prompt(
        "Fast path",
        caller="test/harness",
        cwd=tmp_path,
        task_profile="fast",
        output_format="text",
    )

    assert result.stdout == "FAST OK"
    assert captured["cmd"][:2] == ["/tmp/fake-codex", "exec"]
    assert captured["cmd"][captured["cmd"].index("-m") + 1] == "gpt-5.4-mini"
    config_values = [captured["cmd"][idx + 1] for idx, part in enumerate(captured["cmd"]) if part == "-c"]
    assert 'model_reasoning_effort="medium"' in config_values


def test_run_automation_prompt_falls_back_when_configured_backend_is_unavailable(monkeypatch, tmp_path):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_claude_cli", lambda: "")
    monkeypatch.setattr(agent_runner, "_resolve_codex_cli", lambda: "/tmp/fake-codex")
    monkeypatch.setattr(agent_runner, "_load_client_bootstrap_prompt", lambda client: "You are NEXO.")
    monkeypatch.setattr(agent_runner, "_codex_managed_initial_messages_enabled", lambda: False)
    monkeypatch.setattr(agent_runner, "_record_automation_run", lambda **kwargs: (True, ""))
    monkeypatch.setattr(agent_runner, "load_client_preferences", lambda: {
        "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
        "default_terminal_client": "claude_code",
        "automation_enabled": True,
        "automation_backend": "claude_code",
        "client_runtime_profiles": {
            "claude_code": {"model": "claude-opus-4-7[1m]", "reasoning_effort": "max"},
            "codex": {"model": "gpt-5.4", "reasoning_effort": "high"},
        },
        "automation_task_profiles": {
            "default": {"backend": "", "model": "", "reasoning_effort": ""},
            "fast": {"backend": "codex", "model": "gpt-5.4-mini", "reasoning_effort": "medium"},
            "balanced": {"backend": "", "model": "", "reasoning_effort": ""},
            "deep": {"backend": "claude_code", "model": "claude-opus-4-7[1m]", "reasoning_effort": "max"},
        },
    })

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out_idx = cmd.index("-o") + 1
        with open(cmd[out_idx], "w", encoding="utf-8") as fh:
            fh.write("FALLBACK OK")
        return subprocess.CompletedProcess(cmd, 0, _codex_json_usage(), "")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    result = agent_runner.run_automation_prompt(
        "Fallback path",
        caller="test/harness",
        cwd=tmp_path,
        output_format="text",
    )

    assert result.stdout == "FALLBACK OK"
    assert captured["cmd"][:2] == ["/tmp/fake-codex", "exec"]
    assert captured["cmd"][captured["cmd"].index("-m") + 1] == "gpt-5.4"
    config_values = [captured["cmd"][idx + 1] for idx, part in enumerate(captured["cmd"]) if part == "-c"]
    # v5.10.0: resonance_map tier (test/harness -> maximo) drives the
    # effort now, not client_runtime_profiles. For MAXIMO + codex that
    # is "xhigh". client_runtime_profiles still supplies the model when
    # resonance_map leaves it empty, but effort is always mapped.
    assert 'model_reasoning_effort="xhigh"' in config_values


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
    monkeypatch.setattr(agent_runner, "_record_automation_run", lambda **kwargs: (True, ""))
    monkeypatch.setattr(agent_runner, "load_client_preferences", lambda: {
        "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
        "default_terminal_client": "codex",
        "automation_enabled": True,
        "automation_backend": "codex",
        "client_runtime_profiles": {
            "claude_code": {"model": "claude-opus-4-7[1m]", "reasoning_effort": "max"},
            "codex": {"model": "gpt-5.4-mini", "reasoning_effort": "high"},
        },
    })

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out_idx = cmd.index("-o") + 1
        with open(cmd[out_idx], "w", encoding="utf-8") as fh:
            fh.write("OK")
        return subprocess.CompletedProcess(cmd, 0, _codex_json_usage(), "")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    agent_runner.run_automation_prompt(
        "Do it",
        caller="test/harness",
        cwd=tmp_path,
        model="opus",
        output_format="text",
    )

    # v5.10.0:
    #   - model="opus" is a legacy hint; `_resolve_runtime_model_and_effort`
    #     rewrites it to the configured profile model (gpt-5.4-mini).
    #     The resonance map does NOT override here because the caller
    #     passed an explicit (legacy) model, so mapped_model short-circuits.
    #   - reasoning_effort was empty at entry, so the resonance map fills
    #     it in: test/harness=MAXIMO → codex effort "xhigh".
    assert captured["cmd"][captured["cmd"].index("-m") + 1] == "gpt-5.4-mini"
    config_values = [captured["cmd"][idx + 1] for idx, part in enumerate(captured["cmd"]) if part == "-c"]
    assert 'initial_messages=[{role="system",content="You are NEXO."}]' in config_values
    assert 'model_reasoning_effort="xhigh"' in config_values


def test_codex_backend_uses_configured_profile_when_model_is_empty(monkeypatch, tmp_path):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_codex_cli", lambda: "/tmp/fake-codex")
    monkeypatch.setattr(agent_runner, "_load_client_bootstrap_prompt", lambda client: "You are NEXO.")
    monkeypatch.setattr(agent_runner, "_codex_managed_initial_messages_enabled", lambda: False)
    monkeypatch.setattr(agent_runner, "_record_automation_run", lambda **kwargs: (True, ""))
    monkeypatch.setattr(agent_runner, "load_client_preferences", lambda: {
        "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
        "default_terminal_client": "codex",
        "automation_enabled": True,
        "automation_backend": "codex",
        "client_runtime_profiles": {
            "claude_code": {"model": "claude-opus-4-7[1m]", "reasoning_effort": "max"},
            "codex": {"model": "gpt-5.4-mini", "reasoning_effort": "high"},
        },
    })

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out_idx = cmd.index("-o") + 1
        with open(cmd[out_idx], "w", encoding="utf-8") as fh:
            fh.write("OK")
        return subprocess.CompletedProcess(cmd, 0, _codex_json_usage(), "")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    agent_runner.run_automation_prompt(
        "Do it",
        caller="test/harness",
        cwd=tmp_path,
        model="",
        output_format="text",
    )

    # See note in the prior test: the resonance map (test/harness=MAXIMO)
    # drives the values to gpt-5.4/xhigh in v5.10.0 and onwards.
    assert captured["cmd"][captured["cmd"].index("-m") + 1] == "gpt-5.4"
    config_values = [captured["cmd"][idx + 1] for idx, part in enumerate(captured["cmd"]) if part == "-c"]
    assert 'initial_messages=[{role="system",content="You are NEXO."}]' in config_values
    assert 'model_reasoning_effort="xhigh"' in config_values


def test_codex_runner_skips_inline_bootstrap_when_global_bootstrap_is_managed(monkeypatch, tmp_path):
    import agent_runner

    monkeypatch.setattr(agent_runner, "_resolve_codex_cli", lambda: "/tmp/fake-codex")
    monkeypatch.setattr(agent_runner, "_load_client_bootstrap_prompt", lambda client: "You are NEXO.")
    monkeypatch.setattr(agent_runner, "_codex_managed_initial_messages_enabled", lambda: True)
    monkeypatch.setattr(agent_runner, "_interactive_startup_prompt", lambda client: "Start NEXO now.")

    client, cmd = agent_runner.build_interactive_client_command(
        target=tmp_path,
        preferences={
            "interactive_clients": {"claude_code": True, "codex": True, "claude_desktop": False},
            "default_terminal_client": "codex",
            "automation_enabled": True,
            "automation_backend": "codex",
            "client_runtime_profiles": {
                "claude_code": {"model": "claude-opus-4-7[1m]", "reasoning_effort": "max"},
                "codex": {"model": "gpt-5.4", "reasoning_effort": "xhigh"},
            },
        },
    )

    assert client == "codex"
    assert cmd[:5] == [
        "/tmp/fake-codex",
        "--sandbox",
        "danger-full-access",
        "--ask-for-approval",
        "never",
    ]
    assert "--full-auto" not in cmd
    config_values = [cmd[idx + 1] for idx, part in enumerate(cmd) if part == "-c"]
    assert not any("initial_messages=" in value for value in config_values)
    assert 'model_reasoning_effort="xhigh"' in config_values
    assert cmd[-3:] == ["-C", str(tmp_path), "Start NEXO now."]


def test_launch_interactive_client_uses_target_as_cwd(monkeypatch, tmp_path):
    import agent_runner

    captured = {}

    monkeypatch.setattr(
        agent_runner,
        "build_interactive_client_command",
        lambda **kwargs: ("claude_code", ["/tmp/fake-claude", "--dangerously-skip-permissions", "Start NEXO now."]),
    )

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    result = agent_runner.launch_interactive_client(target=tmp_path)

    assert result.returncode == 0
    assert captured["cmd"][-1] == "Start NEXO now."
    assert captured["cwd"] == str(tmp_path.resolve())


def test_build_followup_terminal_shell_command_uses_codex_interactive_flags(monkeypatch, tmp_path):
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
                "claude_code": {"model": "claude-opus-4-7[1m]", "reasoning_effort": "max"},
                "codex": {"model": "gpt-5.4", "reasoning_effort": "xhigh"},
            },
        },
    )

    assert client == "codex"
    parsed = shlex.split(command)
    assert parsed[:5] == [
        "/tmp/fake-codex",
        "--sandbox",
        "danger-full-access",
        "--ask-for-approval",
        "never",
    ]
    assert "--full-auto" not in parsed
    assert parsed[-1] == "NEXO: execute followup from file $(cat /tmp/followup.txt)"


def test_codex_telemetry_estimates_cost_from_usage_snapshot():
    import agent_runner

    _, telemetry = agent_runner._extract_codex_telemetry(
        _codex_json_usage(input_tokens=1_000_000, cached_input_tokens=0, output_tokens=0),
        final_stdout="OK",
        model="gpt-5.4",
    )

    assert telemetry["usage"]["input_tokens"] == 1_000_000
    assert telemetry["total_cost_usd"] == 1.25
    assert telemetry["cost_source"] == "pricing_snapshot"


def test_claude_telemetry_uses_backend_cost():
    import agent_runner

    _, telemetry = agent_runner._extract_claude_telemetry(
        _claude_json_result("DONE", cost=0.42),
        requested_output_format="text",
    )

    assert telemetry["total_cost_usd"] == 0.42
    assert telemetry["cost_source"] == "backend"
    assert telemetry["usage"]["output_tokens"] == 7
