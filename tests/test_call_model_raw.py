"""Tests for call_model_raw fail-closed behaviour (Fase 2 spec 0.1 + 0.20)."""
from __future__ import annotations

import pytest


def _install_sdk_stubs(monkeypatch, provider_name: str, module):
    """Register a fake SDK module so call_model_raw imports it."""
    import importlib
    import sys
    sys.modules[provider_name] = module
    # If call_model_raw was already imported, bust its cache
    importlib.invalidate_caches()


class _Resp:
    def __init__(self, text: str):
        self.content = [type("B", (), {"text": text})()]


@pytest.fixture
def force_claude_code(monkeypatch):
    from call_model_raw import call_model_raw  # noqa: F401 — import early
    import client_preferences as cp
    import resonance_map as rm
    monkeypatch.setattr(cp, "load_client_preferences", lambda: {
        "automation_backend": cp.CLIENT_CLAUDE_CODE,
        "automation_enabled": True,
    })
    monkeypatch.setattr(cp, "resolve_automation_backend", lambda preferences=None: cp.CLIENT_CLAUDE_CODE)
    # Ensure tier is resolvable
    if "enforcer_classifier" not in rm.SYSTEM_OWNED_CALLERS:
        rm.SYSTEM_OWNED_CALLERS["enforcer_classifier"] = "muy_bajo"
    # Ensure anthropic key present so auth check passes
    monkeypatch.setattr("call_model_raw._resolve_anthropic_key", lambda: "sk-test-key")


def test_automation_none_raises(monkeypatch):
    from call_model_raw import call_model_raw, ClassifierUnavailableError
    import client_preferences as cp
    monkeypatch.setattr(cp, "resolve_automation_backend", lambda preferences=None: cp.BACKEND_NONE)
    with pytest.raises(ClassifierUnavailableError, match="automation_backend=none"):
        call_model_raw("hi")


def test_unregistered_caller_raises(monkeypatch, force_claude_code):
    """Unregistered caller → UnregisteredCallerError wrapped as ClassifierUnavailableError."""
    from call_model_raw import call_model_raw, ClassifierUnavailableError
    with pytest.raises(ClassifierUnavailableError, match="caller not registered"):
        call_model_raw("hi", caller="totally_unknown_caller_for_test_only")


def test_empty_model_raises(monkeypatch, force_claude_code):
    """If resolve_model_and_effort returns empty → ClassifierUnavailableError."""
    from call_model_raw import call_model_raw, ClassifierUnavailableError
    import call_model_raw as cmr
    def fake_resolve(caller, backend, explicit_tier=None):
        return "", ""
    # Patch resolve_model_and_effort *inside* call_model_raw by replacing the
    # symbol the function looked up at import time.
    import resonance_map
    monkeypatch.setattr(resonance_map, "resolve_model_and_effort", fake_resolve)
    with pytest.raises(ClassifierUnavailableError, match="no .model, effort"):
        call_model_raw("hi")


def test_anthropic_success(monkeypatch, force_claude_code):
    from call_model_raw import call_model_raw
    import sys
    class FakeAnthropic:
        APITimeoutError = type("APITimeoutError", (Exception,), {})
        RateLimitError = type("RateLimitError", (Exception,), {})
        APIConnectionError = type("APIConnectionError", (Exception,), {})
        APIStatusError = type("APIStatusError", (Exception,), {"status_code": 500})
        class Anthropic:
            def __init__(self, *a, **k): pass
            class _M:
                def create(self, **kwargs):
                    return _Resp("yes")
            messages = _M()
            def __getattr__(self, name):
                if name == "messages": return self._M()
                raise AttributeError(name)
    fake = FakeAnthropic()
    fake.Anthropic = FakeAnthropic.Anthropic
    sys.modules["anthropic"] = fake
    out = call_model_raw("Q?")
    assert out == "yes"


def test_anthropic_timeout_wraps(monkeypatch, force_claude_code):
    from call_model_raw import call_model_raw, ClassifierUnavailableError
    import sys
    class FakeAnthropic:
        class APITimeoutError(Exception): pass
        class RateLimitError(Exception): pass
        class APIConnectionError(Exception): pass
        class APIStatusError(Exception):
            def __init__(self, *a, **k):
                super().__init__(*a)
                self.status_code = 500
        class Anthropic:
            def __init__(self, *a, **k): pass
            class _M:
                def create(self, **kwargs):
                    raise FakeAnthropic.APITimeoutError("deadline")
            messages = _M()
    sys.modules["anthropic"] = FakeAnthropic
    with pytest.raises(ClassifierUnavailableError, match="anthropic timeout"):
        call_model_raw("Q?")


def test_anthropic_rate_limit_wraps(monkeypatch, force_claude_code):
    from call_model_raw import call_model_raw, ClassifierUnavailableError
    import sys
    class FakeAnthropic:
        class APITimeoutError(Exception): pass
        class RateLimitError(Exception): pass
        class APIConnectionError(Exception): pass
        class APIStatusError(Exception):
            def __init__(self, *a, **k):
                super().__init__(*a)
                self.status_code = 500
        class Anthropic:
            def __init__(self, *a, **k): pass
            class _M:
                def create(self, **kwargs):
                    raise FakeAnthropic.RateLimitError("429")
            messages = _M()
    sys.modules["anthropic"] = FakeAnthropic
    with pytest.raises(ClassifierUnavailableError, match="rate_limit"):
        call_model_raw("Q?")


def test_anthropic_5xx_wraps(monkeypatch, force_claude_code):
    from call_model_raw import call_model_raw, ClassifierUnavailableError
    import sys
    class FakeAnthropic:
        class APITimeoutError(Exception): pass
        class RateLimitError(Exception): pass
        class APIConnectionError(Exception): pass
        class APIStatusError(Exception):
            def __init__(self, msg="bad", status_code=503):
                super().__init__(msg)
                self.status_code = status_code
        class Anthropic:
            def __init__(self, *a, **k): pass
            class _M:
                def create(self, **kwargs):
                    raise FakeAnthropic.APIStatusError("server down", 502)
            messages = _M()
    sys.modules["anthropic"] = FakeAnthropic
    with pytest.raises(ClassifierUnavailableError, match="5xx|502"):
        call_model_raw("Q?")


def test_anthropic_connection_wraps(monkeypatch, force_claude_code):
    from call_model_raw import call_model_raw, ClassifierUnavailableError
    import sys
    class FakeAnthropic:
        class APITimeoutError(Exception): pass
        class RateLimitError(Exception): pass
        class APIConnectionError(Exception): pass
        class APIStatusError(Exception):
            def __init__(self, *a, **k):
                super().__init__(*a)
                self.status_code = 500
        class Anthropic:
            def __init__(self, *a, **k): pass
            class _M:
                def create(self, **kwargs):
                    raise FakeAnthropic.APIConnectionError("dns")
            messages = _M()
    sys.modules["anthropic"] = FakeAnthropic
    with pytest.raises(ClassifierUnavailableError, match="connection"):
        call_model_raw("Q?")


def test_anthropic_missing_key(monkeypatch, force_claude_code):
    from call_model_raw import call_model_raw, ClassifierUnavailableError
    monkeypatch.setattr("call_model_raw._resolve_anthropic_key", lambda: "")
    with pytest.raises(ClassifierUnavailableError, match="no ANTHROPIC_API_KEY"):
        call_model_raw("Q?")
