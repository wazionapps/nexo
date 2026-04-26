"""Tests for the optional LLM endpoint and auth provider overrides.

Standalone behaviour is covered in test_call_model_raw.py. This module
focuses on the override files (~/.nexo/config/llm_endpoint.json,
~/.nexo/config/auth_provider.json) and the alias translation that kicks
in when override mode is active.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture: redirect ~/.nexo/config/ to a temp directory and clear any cached
# override-mode state between tests.
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_config_dir(monkeypatch, tmp_path) -> Path:
    """Point _BRAIN_CONFIG_DIR at a tmp dir so tests don't read the real one."""
    import call_model_raw
    fake_dir = tmp_path / "config"
    fake_dir.mkdir()
    monkeypatch.setattr(call_model_raw, "_BRAIN_CONFIG_DIR", fake_dir)
    monkeypatch.delenv("NEXO_LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return fake_dir


def _write_endpoint(dir_: Path, base_url: str, version: int = 1) -> None:
    (dir_ / "llm_endpoint.json").write_text(
        json.dumps({"version": version, "anthropic_base_url": base_url})
    )


def _write_auth_provider(
    dir_: Path,
    *,
    command: str,
    args: list[str] | None = None,
    timeout_sec: int | None = None,
    version: int = 1,
) -> None:
    payload: dict = {"version": version, "command": command}
    if args is not None:
        payload["args"] = args
    if timeout_sec is not None:
        payload["timeout_sec"] = timeout_sec
    (dir_ / "auth_provider.json").write_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# is_override_mode + resolve_api_base_url
# ---------------------------------------------------------------------------
def test_no_config_files_means_standalone(fake_config_dir):
    from call_model_raw import is_override_mode, resolve_api_base_url
    assert is_override_mode() is False
    assert resolve_api_base_url() == "https://api.anthropic.com"


def test_endpoint_file_activates_override(fake_config_dir):
    from call_model_raw import is_override_mode, resolve_api_base_url
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    assert is_override_mode() is True
    assert resolve_api_base_url() == "https://proxy.example.com/api"


def test_unsupported_version_ignored(fake_config_dir, capsys):
    from call_model_raw import is_override_mode, resolve_api_base_url
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api", version=99)
    assert is_override_mode() is False
    assert resolve_api_base_url() == "https://api.anthropic.com"
    err = capsys.readouterr().err
    assert "version" in err and "not supported" in err


def test_malformed_json_ignored(fake_config_dir, capsys):
    from call_model_raw import is_override_mode
    (fake_config_dir / "llm_endpoint.json").write_text("{ this is not json")
    assert is_override_mode() is False
    err = capsys.readouterr().err
    assert "failed to read override" in err


def test_env_endpoint_does_not_activate_override(fake_config_dir, monkeypatch):
    """NEXO_LLM_ENDPOINT can override the URL but only the file flips override
    mode on. This keeps env-only configurations transparent to standalone
    callers (they keep using direct Anthropic auth)."""
    from call_model_raw import is_override_mode, resolve_api_base_url
    monkeypatch.setenv("NEXO_LLM_ENDPOINT", "https://env-proxy.example.com")
    assert is_override_mode() is False
    assert resolve_api_base_url() == "https://env-proxy.example.com"


def test_internal_force_disable_with_files_present(fake_config_dir, monkeypatch):
    """Internal escape hatch used by the test suite and by maintainers when
    validating regressions against the upstream Anthropic API without
    renaming the override files. Behaviour: the helper returns False even
    when llm_endpoint.json is physically present."""
    from call_model_raw import is_override_mode
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    assert is_override_mode() is True  # baseline: override active
    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv("NEXO_RAW_ANTHROPIC", truthy)
        assert is_override_mode() is False, f"value {truthy!r} should disable override"


def test_internal_force_disable_ignores_falsy_values(fake_config_dir, monkeypatch):
    from call_model_raw import is_override_mode
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    for falsy in ("", "0", "false", "no", "off", "weird"):
        monkeypatch.setenv("NEXO_RAW_ANTHROPIC", falsy)
        assert is_override_mode() is True, f"value {falsy!r} should not disable override"


# ---------------------------------------------------------------------------
# resolve_auth_token + auth_provider command
# ---------------------------------------------------------------------------
def test_resolve_auth_token_no_config_uses_env(fake_config_dir, monkeypatch):
    from call_model_raw import resolve_auth_token
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-token")
    assert resolve_auth_token() == "sk-env-token"


def test_auth_provider_command_returns_stdout(fake_config_dir, tmp_path, monkeypatch):
    """The helper script's stdout (trimmed) becomes the bearer."""
    from call_model_raw import resolve_auth_token
    helper = tmp_path / "auth-helper.sh"
    helper.write_text("#!/bin/sh\nprintf 'sk-helper-token-xyz\\n'\n")
    helper.chmod(0o755)
    _write_auth_provider(fake_config_dir, command=str(helper))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-fallback")  # must NOT win
    assert resolve_auth_token() == "sk-helper-token-xyz"


def test_auth_provider_command_passes_args(fake_config_dir, tmp_path):
    from call_model_raw import resolve_auth_token
    helper = tmp_path / "auth-helper.sh"
    helper.write_text('#!/bin/sh\necho "args: $*"\n')
    helper.chmod(0o755)
    _write_auth_provider(fake_config_dir, command=str(helper), args=["--for", "anthropic"])
    out = resolve_auth_token()
    assert out == "args: --for anthropic"


def test_auth_provider_command_timeout_falls_back(fake_config_dir, monkeypatch, capsys):
    """Learning #294: subprocess timeouts must surface explicitly. We catch
    TimeoutExpired and fall back to env, with a stderr warning."""
    from call_model_raw import resolve_auth_token
    _write_auth_provider(fake_config_dir, command="/usr/bin/sleep", args=["30"], timeout_sec=1)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-after-timeout")

    def _fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 5))

    monkeypatch.setattr(subprocess, "run", _fake_run)
    token = resolve_auth_token()
    assert token == "sk-env-after-timeout"
    err = capsys.readouterr().err
    assert "timed out" in err


def test_auth_provider_command_missing_falls_back(fake_config_dir, monkeypatch, capsys):
    from call_model_raw import resolve_auth_token
    _write_auth_provider(fake_config_dir, command="/no/such/binary-9b9a23")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-after-missing")
    token = resolve_auth_token()
    assert token == "sk-env-after-missing"
    err = capsys.readouterr().err
    assert "auth_provider command failed" in err


def test_auth_provider_command_nonzero_exit_falls_back(fake_config_dir, tmp_path, monkeypatch, capsys):
    from call_model_raw import resolve_auth_token
    helper = tmp_path / "broken.sh"
    helper.write_text("#!/bin/sh\necho 'oops bad' >&2\nexit 7\n")
    helper.chmod(0o755)
    _write_auth_provider(fake_config_dir, command=str(helper))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-after-nonzero")
    token = resolve_auth_token()
    assert token == "sk-env-after-nonzero"
    err = capsys.readouterr().err
    assert "exit=7" in err


def test_auth_provider_empty_stdout_falls_back(fake_config_dir, tmp_path, monkeypatch):
    from call_model_raw import resolve_auth_token
    helper = tmp_path / "empty.sh"
    helper.write_text("#!/bin/sh\necho ''\n")
    helper.chmod(0o755)
    _write_auth_provider(fake_config_dir, command=str(helper))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-after-empty")
    assert resolve_auth_token() == "sk-env-after-empty"


# ---------------------------------------------------------------------------
# call_model_raw end-to-end behaviour with override active
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, text: str):
        self.content = [type("B", (), {"text": text})()]


def _install_fake_anthropic(monkeypatch, captured: dict):
    """Install a fake `anthropic` SDK and capture client init + call args."""
    class FakeAnthropic:
        class APITimeoutError(Exception): pass
        class RateLimitError(Exception): pass
        class APIConnectionError(Exception): pass
        class APIStatusError(Exception):
            def __init__(self, *a, **k):
                super().__init__(*a)
                self.status_code = 500

        class Anthropic:
            def __init__(self, *args, **kwargs):
                captured["client_kwargs"] = dict(kwargs)
                self.messages = FakeAnthropic._Messages()

        class _Messages:
            def create(self, **kwargs):
                captured["create_kwargs"] = dict(kwargs)
                return _Resp("yes")

    sys.modules["anthropic"] = FakeAnthropic
    return FakeAnthropic


@pytest.fixture
def stubbed_resonance(monkeypatch):
    """Pin call_model_raw to a deterministic (model, effort) so the test
    controls exactly which alias should be selected."""
    import client_preferences as cp
    import resonance_map as rm

    monkeypatch.setattr(cp, "resolve_automation_backend", lambda preferences=None: cp.CLIENT_CLAUDE_CODE)
    monkeypatch.setattr(cp, "load_client_preferences", lambda: {
        "automation_backend": cp.CLIENT_CLAUDE_CODE,
        "automation_enabled": True,
    })
    if "enforcer_classifier" not in rm.SYSTEM_OWNED_CALLERS:
        rm.SYSTEM_OWNED_CALLERS["enforcer_classifier"] = "muy_bajo"

    def _force(model: str, effort: str):
        monkeypatch.setattr(
            rm, "resolve_model_and_effort",
            lambda caller, backend, explicit_tier=None: (model, effort),
        )

    return _force


def test_override_translates_to_alias(fake_config_dir, monkeypatch, stubbed_resonance):
    """In override mode the SDK receives the wire alias, not the concrete model."""
    from call_model_raw import call_model_raw
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-bearer-via-env")
    stubbed_resonance("claude-haiku-4-5-20251001", "")
    captured: dict = {}
    _install_fake_anthropic(monkeypatch, captured)

    out = call_model_raw("ping?")
    assert out == "yes"
    assert captured["create_kwargs"]["model"] == "nexo-mini"
    assert captured["client_kwargs"].get("base_url") == "https://proxy.example.com/api"
    assert captured["client_kwargs"].get("api_key") == "sk-bearer-via-env"
    headers = captured["create_kwargs"].get("extra_headers") or {}
    assert "Idempotency-Key" in headers
    assert len(headers["Idempotency-Key"]) >= 16


def test_override_alias_for_max_tier(fake_config_dir, monkeypatch, stubbed_resonance):
    from call_model_raw import call_model_raw
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-bearer")
    stubbed_resonance("claude-opus-4-7[1m]", "max")
    captured: dict = {}
    _install_fake_anthropic(monkeypatch, captured)
    call_model_raw("Q?")
    assert captured["create_kwargs"]["model"] == "nexo-max"


def test_override_alias_for_each_tier(fake_config_dir, monkeypatch, stubbed_resonance):
    from call_model_raw import call_model_raw
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-bearer")
    cases = [
        (("claude-opus-4-7[1m]", "max"),    "nexo-max"),
        (("claude-opus-4-7[1m]", "xhigh"),  "nexo-high"),
        (("claude-opus-4-7[1m]", "high"),   "nexo-medium"),
        (("claude-opus-4-7[1m]", "medium"), "nexo-low"),
        (("claude-haiku-4-5-20251001", ""), "nexo-mini"),
    ]
    for (model, effort), expected_alias in cases:
        stubbed_resonance(model, effort)
        captured: dict = {}
        _install_fake_anthropic(monkeypatch, captured)
        call_model_raw("Q?")
        assert captured["create_kwargs"]["model"] == expected_alias, (
            f"({model}, {effort}) -> expected {expected_alias}, "
            f"got {captured['create_kwargs']['model']}"
        )


def test_override_unmapped_pair_raises(fake_config_dir, monkeypatch, stubbed_resonance):
    """Surface unmapped (model, effort) locally as ClassifierUnavailableError
    instead of letting the proxy reject with a 400."""
    from call_model_raw import call_model_raw, ClassifierUnavailableError
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-bearer")
    stubbed_resonance("claude-mystery-12-99", "ludicrous")
    _install_fake_anthropic(monkeypatch, {})
    with pytest.raises(ClassifierUnavailableError, match="no alias mapped"):
        call_model_raw("Q?")


def test_override_no_bearer_raises(fake_config_dir, monkeypatch, stubbed_resonance):
    from call_model_raw import call_model_raw, ClassifierUnavailableError
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    # No env var, no auth_provider, no fallback files (filesystem patched).
    monkeypatch.setattr("call_model_raw._resolve_anthropic_key", lambda: "")
    stubbed_resonance("claude-opus-4-7[1m]", "max")
    _install_fake_anthropic(monkeypatch, {})
    with pytest.raises(ClassifierUnavailableError, match="no bearer"):
        call_model_raw("Q?")


def test_override_uses_auth_provider_command(fake_config_dir, tmp_path, monkeypatch, stubbed_resonance):
    """When override mode is on AND auth_provider is configured, the bearer
    must come from the helper's stdout, not from env."""
    from call_model_raw import call_model_raw
    _write_endpoint(fake_config_dir, "https://proxy.example.com/api")
    helper = tmp_path / "auth.sh"
    helper.write_text("#!/bin/sh\nprintf 'sk-helper-bearer-007'\n")
    helper.chmod(0o755)
    _write_auth_provider(fake_config_dir, command=str(helper))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-must-not-win")
    stubbed_resonance("claude-opus-4-7[1m]", "xhigh")
    captured: dict = {}
    _install_fake_anthropic(monkeypatch, captured)

    call_model_raw("Q?")
    assert captured["client_kwargs"]["api_key"] == "sk-helper-bearer-007"
    assert captured["create_kwargs"]["model"] == "nexo-high"


def test_standalone_does_not_send_idempotency_key(fake_config_dir, monkeypatch, stubbed_resonance):
    """In standalone the request must look exactly like before V11: no
    extra_headers, no alias translation, direct concrete model."""
    from call_model_raw import call_model_raw
    monkeypatch.setattr("call_model_raw._resolve_anthropic_key", lambda: "sk-standalone")
    stubbed_resonance("claude-opus-4-7[1m]", "max")
    captured: dict = {}
    _install_fake_anthropic(monkeypatch, captured)
    call_model_raw("Q?")
    assert captured["create_kwargs"]["model"] == "claude-opus-4-7[1m]"
    assert "extra_headers" not in captured["create_kwargs"]
    assert "base_url" not in captured["client_kwargs"]
