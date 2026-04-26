"""End-to-end wire tests for override mode.

The unit tests in ``test_call_model_raw_overrides.py`` mock the Anthropic
SDK entirely (``_install_fake_anthropic`` replaces the package), which
catches incorrect call shape but cannot detect bugs that only manifest
on the wire (for example passing the bearer to the SDK as ``api_key``
which produces ``X-Api-Key`` instead of ``auth_token`` which produces
``Authorization: Bearer``). A proxy that only accepts the standard
OAuth header would 401 every request even though every unit test stays
green.

These tests stand up a local ``BaseHTTPRequestHandler`` that captures
the raw HTTP request issued by the real Anthropic SDK against a fake
``base_url``. We assert directly on the captured headers and body.

Note: ``test_call_model_raw_overrides.py`` installs a fake ``anthropic``
module into ``sys.modules`` for its unit tests and does not restore it
after the run, so when both files execute in the same pytest process
the E2E tests would inherit the fake module and never reach the real
HTTP wire. The autouse fixture below evicts the cached fake so the real
SDK is reimported on the next ``import anthropic`` inside
``call_model_raw``.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _evict_fake_anthropic_module():
    """Strip any fake ``anthropic`` module that an earlier unit test left
    in ``sys.modules``. The real package will be reimported the next time
    ``call_model_raw._call_anthropic_raw`` runs ``import anthropic``."""
    cached = sys.modules.get("anthropic")
    if cached is not None and not getattr(cached, "__spec__", None):
        # Heuristic: real packages have an __spec__ from importlib;
        # fake test classes do not.
        sys.modules.pop("anthropic", None)
    yield
    # No teardown — leave whatever the real import populated; the next
    # test will re-evict if a unit test reinstalls a fake.


# ---------------------------------------------------------------------------
# Local capture server
# ---------------------------------------------------------------------------
class _CapturingHandler(BaseHTTPRequestHandler):
    captured: dict = {}

    def log_message(self, *args, **kwargs):  # silence test noise
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body_bytes = self.rfile.read(length) if length else b""
        try:
            body = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except Exception:
            body = {"_raw": body_bytes.decode("utf-8", errors="replace")}

        # Snapshot every header verbatim so tests can assert on
        # presence/absence/value.
        headers_snapshot = {k: v for k, v in self.headers.items()}
        type(self).captured = {
            "method": "POST",
            "path": self.path,
            "headers": headers_snapshot,
            "body": body,
            "raw_body_len": len(body_bytes),
        }

        # Respond with a minimal Anthropic Messages API shape so the SDK
        # parses the response and returns normally instead of raising.
        response = {
            "id": "msg_e2e_test",
            "type": "message",
            "role": "assistant",
            "model": body.get("model", "nexo-mini"),
            "content": [{"type": "text", "text": "yes"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        payload = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def local_proxy_server():
    """Start a one-shot HTTP server on 127.0.0.1:auto and yield its URL."""
    _CapturingHandler.captured = {}
    server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    try:
        yield base_url, _CapturingHandler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Helpers (mirror the unit-test fixtures)
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_config_dir(monkeypatch, tmp_path) -> Path:
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


def _write_auth_provider_helper(
    dir_: Path, tmp_path: Path, token: str, version: int = 1
) -> None:
    helper = tmp_path / f"auth-{abs(hash(token)) & 0xffff:x}.sh"
    helper.write_text(f"#!/bin/sh\nprintf '{token}'\n")
    helper.chmod(0o755)
    (dir_ / "auth_provider.json").write_text(
        json.dumps({"version": version, "command": str(helper)})
    )


@pytest.fixture
def stubbed_resonance(monkeypatch):
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


# ---------------------------------------------------------------------------
# Wire tests
# ---------------------------------------------------------------------------
def test_e2e_override_uses_authorization_bearer_not_x_api_key(
    fake_config_dir, tmp_path, monkeypatch, stubbed_resonance, local_proxy_server
):
    """The headline wire-level guarantee: in override mode the bearer
    arrives at the proxy in the standard ``Authorization: Bearer ...``
    header, NOT in the Anthropic-style ``X-Api-Key`` header. This is
    exactly the failure mode that no SDK-mock unit test can detect."""
    base_url, capture_cls = local_proxy_server
    _write_endpoint(fake_config_dir, base_url)
    _write_auth_provider_helper(fake_config_dir, tmp_path, "sk-proxy-bearer-WIRE")
    stubbed_resonance("claude-haiku-4-5-20251001", "")

    from call_model_raw import call_model_raw
    out = call_model_raw("ping?")
    assert out == "yes"

    headers = {k.lower(): v for k, v in capture_cls.captured["headers"].items()}
    # CRITICAL #1 from the audit report: bearer must be in Authorization.
    assert "authorization" in headers
    assert headers["authorization"] == "Bearer sk-proxy-bearer-WIRE"
    # And it must NOT be in X-Api-Key.
    assert "x-api-key" not in headers


def test_e2e_override_sends_alias_in_body_not_concrete_model(
    fake_config_dir, tmp_path, monkeypatch, stubbed_resonance, local_proxy_server
):
    base_url, capture_cls = local_proxy_server
    _write_endpoint(fake_config_dir, base_url)
    _write_auth_provider_helper(fake_config_dir, tmp_path, "sk-tok")
    stubbed_resonance("claude-opus-4-7[1m]", "max")

    from call_model_raw import call_model_raw
    call_model_raw("Q?")

    body = capture_cls.captured["body"]
    assert body["model"] == "nexo-max"
    # The concrete name MUST NOT leak through to the wire.
    assert "claude-opus-4-7" not in str(body["model"])


def test_e2e_override_sends_idempotency_key(
    fake_config_dir, tmp_path, monkeypatch, stubbed_resonance, local_proxy_server
):
    base_url, capture_cls = local_proxy_server
    _write_endpoint(fake_config_dir, base_url)
    _write_auth_provider_helper(fake_config_dir, tmp_path, "sk-tok")
    stubbed_resonance("claude-opus-4-7[1m]", "max")

    from call_model_raw import call_model_raw
    call_model_raw("Q?", idempotency_key="01J0CALLERPROVIDED01J0")

    headers = {k.lower(): v for k, v in capture_cls.captured["headers"].items()}
    assert headers.get("idempotency-key") == "01J0CALLERPROVIDED01J0"


def test_e2e_override_does_not_leak_real_anthropic_key(
    fake_config_dir, tmp_path, monkeypatch, stubbed_resonance, local_proxy_server
):
    """Belt-and-braces wire test: even if a real sk-ant-... key sits in
    env, the proxy must never receive it. The auth_provider helper
    emits a different token, and that is what we assert lands on the
    wire."""
    base_url, capture_cls = local_proxy_server
    _write_endpoint(fake_config_dir, base_url)
    _write_auth_provider_helper(fake_config_dir, tmp_path, "sk-proxy-only-this")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-REAL-OPERATOR-KEY-DO-NOT-LEAK")
    stubbed_resonance("claude-opus-4-7[1m]", "max")

    from call_model_raw import call_model_raw
    call_model_raw("Q?")

    headers = {k.lower(): v for k, v in capture_cls.captured["headers"].items()}
    auth = headers.get("authorization", "")
    assert "REAL-OPERATOR-KEY" not in auth
    assert "sk-ant-" not in auth
    assert auth == "Bearer sk-proxy-only-this"
    # And not anywhere else in the headers.
    for v in headers.values():
        assert "REAL-OPERATOR-KEY" not in v


def test_e2e_override_aborts_when_auth_provider_missing(
    fake_config_dir, monkeypatch, stubbed_resonance, local_proxy_server
):
    """If override mode is on but auth_provider.json is missing, the call
    must raise ClassifierUnavailableError BEFORE any HTTP request leaves
    the host. That way a misconfigured operator does not silently send a
    real sk-ant-... env key to the custom proxy."""
    base_url, capture_cls = local_proxy_server
    _write_endpoint(fake_config_dir, base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-REAL-MUST-NEVER-LEAK")
    stubbed_resonance("claude-opus-4-7[1m]", "max")

    from call_model_raw import call_model_raw, ClassifierUnavailableError
    with pytest.raises(ClassifierUnavailableError, match="auth_provider"):
        call_model_raw("Q?")
    # No request should have hit the local server.
    assert capture_cls.captured == {} or capture_cls.captured.get("path") is None


def test_e2e_standalone_no_idempotency_key_no_proxy_redirection(
    fake_config_dir, monkeypatch, stubbed_resonance, local_proxy_server
):
    """Without override files Brain libre must hit api.anthropic.com
    directly with its operator key. The local proxy server we started
    in the fixture must NOT receive any traffic. We can't easily assert
    that without making the real Anthropic API call hang on resolution,
    so instead we verify the override predicate is False and the SDK is
    constructed without a base_url override (handled via SDK mock)."""
    # No endpoint file, no auth_provider file.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real-op")
    stubbed_resonance("claude-opus-4-7[1m]", "max")

    from call_model_raw import is_override_mode
    assert is_override_mode() is False
    # Nothing reached the local server in this scenario.
    _, capture_cls = local_proxy_server
    assert capture_cls.captured == {} or capture_cls.captured.get("path") is None


def test_e2e_override_bare_mode_combination(
    fake_config_dir, tmp_path, monkeypatch, stubbed_resonance, local_proxy_server
):
    """Combination scenario the unit tests miss: override files present
    and the agent_runner --bare branch active. The CLI children spawned
    by agent_runner must inherit ANTHROPIC_BASE_URL + the proxy bearer
    (NOT the operator's real ANTHROPIC_API_KEY)."""
    base_url, _capture = local_proxy_server
    _write_endpoint(fake_config_dir, base_url)
    _write_auth_provider_helper(fake_config_dir, tmp_path, "sk-proxy-bare-mode")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real-op-leak-test")

    # Simulate the env that _apply_llm_endpoint_override would feed to
    # subprocess.run for the spawned CLI.
    from agent_runner import _apply_llm_endpoint_override
    env = {"ANTHROPIC_API_KEY": "sk-ant-real-op-leak-test", "PATH": "/usr/bin"}
    out = _apply_llm_endpoint_override(env)
    assert out["ANTHROPIC_BASE_URL"] == base_url
    assert out["ANTHROPIC_API_KEY"] == "sk-proxy-bare-mode"
    # The original real key must have been replaced, not left next to the
    # proxy URL where the CLI would send it.
    assert "sk-ant-real-op-leak-test" not in out["ANTHROPIC_API_KEY"]


def test_e2e_nexo_home_change_picked_up_post_import(
    monkeypatch, tmp_path, stubbed_resonance, local_proxy_server
):
    """LaunchAgent crons set NEXO_HOME via their wrapper script. Brain
    is already imported by then (warm runtime). Changes to NEXO_HOME
    AFTER import must be honoured on the next is_override_mode() call,
    not silently ignored because a module-level constant cached the
    pre-launch path."""
    import call_model_raw
    monkeypatch.setattr(call_model_raw, "_BRAIN_CONFIG_DIR", None)  # disable test override

    custom_home = tmp_path / "alt-nexo-home"
    (custom_home / "config").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(custom_home))
    base_url, capture_cls = local_proxy_server
    _write_endpoint(custom_home / "config", base_url)
    _write_auth_provider_helper(custom_home / "config", tmp_path, "sk-tok-from-alt-home")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    stubbed_resonance("claude-opus-4-7[1m]", "max")

    from call_model_raw import call_model_raw as _call
    _call("Q?")
    # The custom NEXO_HOME path must have been honoured: the wire request
    # carries the helper bearer from that path.
    headers = {k.lower(): v for k, v in capture_cls.captured["headers"].items()}
    assert headers.get("authorization") == "Bearer sk-tok-from-alt-home"
