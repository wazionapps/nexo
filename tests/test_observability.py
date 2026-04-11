"""Tests for the OTEL observability soft-import — Fase 5 item 2."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _fresh_observability():
    """Reload the module so cached _otel_available is reset between tests."""
    sys.modules.pop("observability", None)
    import observability
    importlib.reload(observability)
    observability._reset_for_tests()
    return observability


# ── is_otel_enabled ───────────────────────────────────────────────────────


class TestIsOtelEnabled:
    def test_returns_false_without_env_var(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
        obs = _fresh_observability()
        assert obs.is_otel_enabled() is False

    def test_returns_false_with_empty_env_var(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
        obs = _fresh_observability()
        assert obs.is_otel_enabled() is False

    def test_caches_result_after_first_call(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
        obs = _fresh_observability()
        assert obs.is_otel_enabled() is False
        # Even if we set the env now, the cached result wins until reset.
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        assert obs.is_otel_enabled() is False  # cache still says False
        obs._reset_for_tests()
        # After reset, check is re-evaluated. May still be False if
        # opentelemetry-api is not installed in this venv (which is the
        # default for the NEXO test env).
        result = obs.is_otel_enabled()
        assert isinstance(result, bool)


# ── tool_span no-op path ─────────────────────────────────────────────────


class TestToolSpanNoOp:
    def test_yields_none_when_otel_disabled(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
        obs = _fresh_observability()
        with obs.tool_span("test") as span:
            assert span is None

    def test_inner_block_runs_normally_when_disabled(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        obs = _fresh_observability()
        ran = []
        with obs.tool_span("test"):
            ran.append("inside")
        assert ran == ["inside"]

    def test_attributes_argument_is_ignored_when_disabled(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        obs = _fresh_observability()
        # Must not raise even with arbitrary nested attribute payloads.
        with obs.tool_span("test", attributes={"nested": {"a": 1}, "list": [1, 2, 3]}):
            pass

    def test_exceptions_propagate_when_disabled(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        obs = _fresh_observability()
        with pytest.raises(ValueError):
            with obs.tool_span("test"):
                raise ValueError("propagate me")


# ── record_tool_attributes ───────────────────────────────────────────────


class TestRecordToolAttributes:
    def test_no_op_on_none_span(self):
        from observability import record_tool_attributes
        # Must not raise.
        record_tool_attributes(None, {"key": "value"})
        record_tool_attributes(None, {})

    def test_no_op_on_empty_attrs(self):
        from observability import record_tool_attributes
        record_tool_attributes(object(), {})


# ── tool_span enabled path with mock tracer ──────────────────────────────


class TestToolSpanWithMockTracer:
    def test_records_attributes_and_status_ok(self, monkeypatch):
        # Force is_otel_enabled to return True
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        obs = _fresh_observability()
        # Skip the test if opentelemetry-api is genuinely missing.
        try:
            import opentelemetry  # noqa: F401
        except ImportError:
            pytest.skip("opentelemetry-api not installed")

        # Replace the tracer with a recording mock.
        recorded_attrs: dict = {}
        recorded_statuses: list = []

        class _MockSpan:
            def set_attribute(self, key, value):
                recorded_attrs[key] = value

            def set_status(self, status):
                recorded_statuses.append(status)

            def record_exception(self, exc):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class _MockCtx:
            def __enter__(self):
                return _MockSpan()

            def __exit__(self, *args):
                return False

        class _MockTracer:
            def start_as_current_span(self, name):
                return _MockCtx()

        monkeypatch.setattr(obs, "_tracer", _MockTracer())
        monkeypatch.setattr(obs, "_otel_available", True)

        with obs.tool_span("nexo_heartbeat", attributes={"sid": "abc", "n": 42}) as span:
            assert span is not None
            obs.record_tool_attributes(span, {"extra": "value"})

        assert recorded_attrs.get("sid") == "abc"
        assert recorded_attrs.get("n") == 42
        assert recorded_attrs.get("extra") == "value"

    def test_records_exception_status_on_raise(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        obs = _fresh_observability()
        try:
            import opentelemetry  # noqa: F401
        except ImportError:
            pytest.skip("opentelemetry-api not installed")

        recorded_statuses: list = []
        recorded_exceptions: list = []

        class _MockSpan:
            def set_attribute(self, *_a, **_k):
                pass

            def set_status(self, status):
                recorded_statuses.append(status)

            def record_exception(self, exc):
                recorded_exceptions.append(exc)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class _MockCtx:
            def __enter__(self):
                return _MockSpan()

            def __exit__(self, *args):
                return False

        class _MockTracer:
            def start_as_current_span(self, name):
                return _MockCtx()

        monkeypatch.setattr(obs, "_tracer", _MockTracer())
        monkeypatch.setattr(obs, "_otel_available", True)

        with pytest.raises(RuntimeError):
            with obs.tool_span("test"):
                raise RuntimeError("boom")

        assert len(recorded_exceptions) == 1
        assert isinstance(recorded_exceptions[0], RuntimeError)
        assert len(recorded_statuses) == 1


# ── Integration: heartbeat wraps with tool_span ──────────────────────────


class TestHeartbeatHonorsObservability:
    def test_heartbeat_runs_without_otel_environment(self, isolated_db, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
        # Reload tools_sessions so its top-level imports re-bind to a
        # fresh observability module that recomputes is_otel_enabled.
        sys.modules.pop("observability", None)
        sys.modules.pop("tools_sessions", None)
        from db import register_session
        register_session("nexo-9501-1501", "obs test")
        from tools_sessions import handle_heartbeat
        result = handle_heartbeat(
            sid="nexo-9501-1501",
            task="otel disabled smoke",
            context_hint="just verifying the no-op path",
        )
        assert isinstance(result, str)
        assert "nexo-9501-1501" in result
