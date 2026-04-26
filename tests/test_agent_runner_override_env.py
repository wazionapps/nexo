"""Tests for the LLM endpoint override env injection in agent_runner.

The Brain SDK direct path (call_model_raw.py) is covered by
test_call_model_raw_overrides.py. This module covers the symmetric
behaviour for child CLI spawns: when ``~/.nexo/config/llm_endpoint.json``
is present, ``_apply_llm_endpoint_override`` rewrites the spawned
environment so the child Anthropic-compatible CLI hits the proxy
instead of api.anthropic.com.

Without this injection a Brain user with NEXO Desktop installed would
keep paying real Anthropic costs (or simply fail-closed if the only
configured bearer is a proxy token) for every cron invocation —
deep-sleep, evolution, followup-runner, morning-agent, email-monitor.
The fix lives in agent_runner.py because LaunchAgent crons do NOT
inherit the env from the Desktop UI process.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def fake_config_dir(monkeypatch, tmp_path) -> Path:
    """Point ``call_model_raw._BRAIN_CONFIG_DIR`` at a tmp dir so the
    override-mode probe is deterministic regardless of what the developer
    happens to have in their real ``~/.nexo/config/``."""
    import call_model_raw
    fake_dir = tmp_path / "nexo-config"
    fake_dir.mkdir()
    monkeypatch.setattr(call_model_raw, "_BRAIN_CONFIG_DIR", fake_dir)
    monkeypatch.delenv("NEXO_LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    return fake_dir


def _write_endpoint(dir_: Path, base_url: str, version: int = 1) -> None:
    (dir_ / "llm_endpoint.json").write_text(
        json.dumps({"version": version, "anthropic_base_url": base_url})
    )


def _write_auth_provider(dir_: Path, *, command: str, args=None, version: int = 1) -> None:
    payload: dict = {"version": version, "command": command}
    if args is not None:
        payload["args"] = list(args)
    (dir_ / "auth_provider.json").write_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# _apply_llm_endpoint_override
# ---------------------------------------------------------------------------
def test_no_override_leaves_env_intact(fake_config_dir, monkeypatch):
    """No config files → standalone path. Child env is whatever the parent
    already had; we MUST NOT inject a proxy URL or strip the operator key."""
    from agent_runner import _apply_llm_endpoint_override
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real-anthropic")
    env = {"ANTHROPIC_API_KEY": "sk-real-anthropic", "FOO": "bar"}
    out = _apply_llm_endpoint_override(env)
    assert out["ANTHROPIC_API_KEY"] == "sk-real-anthropic"
    assert out["FOO"] == "bar"
    assert "ANTHROPIC_BASE_URL" not in out


def test_override_injects_base_url_and_bearer(fake_config_dir, tmp_path):
    """With a valid llm_endpoint.json + auth_provider.json the helper sets
    both ANTHROPIC_BASE_URL and ANTHROPIC_API_KEY for the child."""
    from agent_runner import _apply_llm_endpoint_override
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    helper = tmp_path / "auth.sh"
    helper.write_text("#!/bin/sh\nprintf 'sk-proxy-bearer-42'\n")
    helper.chmod(0o755)
    _write_auth_provider(fake_config_dir, command=str(helper))

    env: dict = {}
    out = _apply_llm_endpoint_override(env)
    assert out["ANTHROPIC_BASE_URL"] == "https://proxy.example.com/api"
    assert out["ANTHROPIC_API_KEY"] == "sk-proxy-bearer-42"


def test_override_overrides_existing_anthropic_api_key(fake_config_dir, tmp_path, monkeypatch):
    """If the parent process happens to have a real ANTHROPIC_API_KEY in env
    (operator's raw sk-ant-... key), override mode replaces it with the
    proxy bearer resolved from auth_provider.json. Override mode now
    REQUIRES auth_provider — the legacy env fallback in override mode
    has been removed to prevent leaking the real key to a custom proxy."""
    from agent_runner import _apply_llm_endpoint_override
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    helper = tmp_path / "auth.sh"
    helper.write_text("#!/bin/sh\nprintf 'sk-proxy-bearer-via-helper'\n")
    helper.chmod(0o755)
    _write_auth_provider(fake_config_dir, command=str(helper))
    env = {"ANTHROPIC_API_KEY": "sk-ant-stale-real-key", "OTHER": "kept"}
    out = _apply_llm_endpoint_override(env)
    assert out["ANTHROPIC_API_KEY"] == "sk-proxy-bearer-via-helper"
    assert out["OTHER"] == "kept"


def test_override_without_auth_provider_does_not_leak_real_key(fake_config_dir, monkeypatch):
    """Critical security guarantee for the CLI spawn path: if override
    mode is active but auth_provider.json is missing, the spawned env
    must NOT carry a real sk-ant-... operator key as the proxy bearer.
    The helper leaves the existing env intact (no override applied) so
    the spawn can decide to fail explicitly elsewhere instead of
    silently leaking."""
    from agent_runner import _apply_llm_endpoint_override
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    # No auth_provider.json written — override should NOT inject anything
    # claiming to be a proxy bearer.
    env = {"ANTHROPIC_API_KEY": "sk-ant-REAL-OPERATOR-KEY", "OTHER": "kept"}
    out = _apply_llm_endpoint_override(env)
    # Either the helper leaves the real key untouched (no proxy
    # redirection) or it sets ANTHROPIC_BASE_URL but blanks the bearer.
    # In both cases the real sk-ant- key MUST NOT be paired with the
    # proxy URL silently.
    if out.get("ANTHROPIC_BASE_URL") == "https://proxy.example.com/api":
        # Override active: bearer must NOT be the real operator key.
        assert out.get("ANTHROPIC_API_KEY") != "sk-ant-REAL-OPERATOR-KEY"
    else:
        # Override skipped: env intact.
        assert out["ANTHROPIC_API_KEY"] == "sk-ant-REAL-OPERATOR-KEY"
    assert out["OTHER"] == "kept"


def test_unsupported_version_falls_back_to_standalone(fake_config_dir, monkeypatch):
    from agent_runner import _apply_llm_endpoint_override
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api", version=99)
    env = {"ANTHROPIC_API_KEY": "sk-keep"}
    out = _apply_llm_endpoint_override(env)
    assert "ANTHROPIC_BASE_URL" not in out
    assert out["ANTHROPIC_API_KEY"] == "sk-keep"


def test_malformed_json_does_not_crash(fake_config_dir):
    from agent_runner import _apply_llm_endpoint_override
    (fake_config_dir / "llm_endpoint.json").write_text("not json {")
    env = {"X": "y"}
    out = _apply_llm_endpoint_override(env)
    assert out == {"X": "y"}


def test_helper_is_defensive_when_call_model_raw_missing(monkeypatch):
    """If call_model_raw cannot be imported for any reason (frozen install,
    sys.path quirk), the helper must not bring down the headless run."""
    import importlib
    import sys
    import agent_runner

    real_module = sys.modules.get("call_model_raw")
    sys.modules["call_model_raw"] = None  # forces ImportError on next import

    try:
        env = {"FOO": "bar"}
        out = agent_runner._apply_llm_endpoint_override(env)
        assert out == {"FOO": "bar"}
    finally:
        if real_module is not None:
            sys.modules["call_model_raw"] = real_module
        else:
            sys.modules.pop("call_model_raw", None)
        importlib.reload(agent_runner)


# ---------------------------------------------------------------------------
# _headless_env composes _apply_llm_endpoint_override
# ---------------------------------------------------------------------------
def test_headless_env_in_standalone_does_not_set_base_url(fake_config_dir, monkeypatch):
    from agent_runner import _headless_env
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-keep")
    env = _headless_env()
    assert env["NEXO_HEADLESS"] == "1"
    assert env["NEXO_AUTOMATION"] == "1"
    assert "ANTHROPIC_BASE_URL" not in env
    assert env["ANTHROPIC_API_KEY"] == "sk-keep"


def test_headless_env_in_override_injects_proxy(fake_config_dir, tmp_path, monkeypatch):
    from agent_runner import _headless_env
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    helper = tmp_path / "auth.sh"
    helper.write_text("#!/bin/sh\nprintf 'sk-proxy-bearer-007'\n")
    helper.chmod(0o755)
    _write_auth_provider(fake_config_dir, command=str(helper))
    # Even if the parent has a real ANTHROPIC_API_KEY, the helper must
    # replace it with the proxy bearer.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real-must-not-leak")

    env = _headless_env()
    assert env["ANTHROPIC_BASE_URL"] == "https://proxy.example.com/api"
    assert env["ANTHROPIC_API_KEY"] == "sk-proxy-bearer-007"
    # The other headless tags survive untouched.
    assert env["NEXO_HEADLESS"] == "1"
    assert env["NEXO_AUTOMATION"] == "1"


def test_headless_env_extra_env_param_takes_precedence_then_override_runs(
    fake_config_dir, tmp_path, monkeypatch
):
    """The ``env`` arg of _headless_env layers on top of os.environ; the
    override helper runs LAST so a misconfigured caller cannot bypass the
    proxy redirection by passing a stale ANTHROPIC_API_KEY in env."""
    from agent_runner import _headless_env
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    helper = tmp_path / "auth.sh"
    helper.write_text("#!/bin/sh\nprintf 'sk-proxy-final-bearer'\n")
    helper.chmod(0o755)
    _write_auth_provider(fake_config_dir, command=str(helper))

    out = _headless_env({"ANTHROPIC_API_KEY": "sk-attempted-bypass", "MARKER": "v"})
    assert out["ANTHROPIC_API_KEY"] == "sk-proxy-final-bearer"
    assert out["MARKER"] == "v"
