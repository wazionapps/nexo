"""OpenTelemetry observability for NEXO Brain — Fase 5 item 2.

Closes Fase 5 item 2 of NEXO-AUDIT-2026-04-11. The audit asked for
observability with OTEL / Langfuse / Phoenix integration. This module
is the soft-import host: it adds tracing primitives that NEXO can call
unconditionally, but only emit real spans when:

  1. The opentelemetry-api package is installed in the user's environment
     (`pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp`).
  2. The OTEL_EXPORTER_OTLP_ENDPOINT environment variable is set
     (or OTEL_SERVICE_NAME is set, indicating the user already
     bootstrapped a tracer provider externally).

Otherwise every primitive is a no-op so the runtime cost on installs
without OTEL is exactly zero (no try/except per call site, no extra
import time on the hot path).

Why this design:
  - NEXO core does NOT require opentelemetry as a hard dependency.
    The 10k+ active users would have an extra 30 MB in their venv with
    no benefit unless they opted in.
  - Users who DO want telemetry get a single env var to flip on.
  - The shape (span name, attributes, status) follows the OpenTelemetry
    semantic conventions for "ai.tool" so dashboards in Langfuse,
    Arize Phoenix, Honeycomb, Jaeger, and Grafana Tempo all render
    NEXO traces with their built-in views.

Usage:

    from observability import tool_span

    with tool_span("nexo_heartbeat", attributes={"sid": sid}) as span:
        result = handle_heartbeat(sid, task)
        if span is not None:
            span.set_attribute("nexo.heartbeat.task", task[:200])
        return result

The `with` block always works whether or not OTEL is installed; the
span is None when telemetry is disabled.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator


# ── OTEL availability detection ──────────────────────────────────────────


_otel_available: bool | None = None
_tracer = None  # cached after first use


def _truthy_env(var: str) -> bool:
    return bool((os.environ.get(var) or "").strip())


def is_otel_enabled() -> bool:
    """Return True if OpenTelemetry is installed AND a configuration is set.

    The two activation conditions are:
      - opentelemetry-api importable
      - One of OTEL_EXPORTER_OTLP_ENDPOINT or OTEL_SERVICE_NAME is set
        (the latter signals an externally-bootstrapped tracer provider).

    Cached after first call so the hot path is a single bool lookup.
    """
    global _otel_available
    if _otel_available is not None:
        return _otel_available

    if not (_truthy_env("OTEL_EXPORTER_OTLP_ENDPOINT") or _truthy_env("OTEL_SERVICE_NAME")):
        _otel_available = False
        return False

    try:
        import opentelemetry  # noqa: F401
        from opentelemetry import trace  # noqa: F401
    except ImportError:
        _otel_available = False
        return False

    _otel_available = True
    return True


def get_tracer():
    """Return a cached opentelemetry.trace.Tracer or None when OTEL is off.

    Lazy: only constructs the tracer the first time it is needed so
    installs without OTEL never pay any import cost.
    """
    global _tracer
    if _tracer is not None:
        return _tracer
    if not is_otel_enabled():
        return None
    try:
        from opentelemetry import trace
        _tracer = trace.get_tracer("nexo-brain")
        return _tracer
    except Exception:
        return None


# ── span context manager ─────────────────────────────────────────────────


@contextmanager
def tool_span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Context manager that emits an OTEL span when telemetry is enabled.

    The span name follows the OTEL semantic convention prefix "ai.tool."
    so dashboards that already group by ai.tool.* automatically pick
    NEXO traces up.

    On success: status = OK.
    On exception: status = ERROR with the exception message recorded as
    an attribute, and the exception is re-raised so callers see it.

    When telemetry is disabled, the context manager yields None and
    does nothing else — the cost is one is_otel_enabled() bool lookup
    plus the empty `with` block, which the Python compiler optimizes.

    Args:
        name: short tool name. The full span name becomes "ai.tool.<name>".
        attributes: optional dict of OTEL attributes to set on the span.
    """
    if not is_otel_enabled():
        yield None
        return

    tracer = get_tracer()
    if tracer is None:
        yield None
        return

    try:
        from opentelemetry import trace as _trace
        span_name = f"ai.tool.{name}" if not name.startswith("ai.tool.") else name
        with tracer.start_as_current_span(span_name) as span:
            try:
                if attributes:
                    for key, value in attributes.items():
                        try:
                            span.set_attribute(key, value)
                        except Exception:
                            # Some values (e.g. dicts) are not OTEL-compatible.
                            try:
                                span.set_attribute(key, str(value)[:1000])
                            except Exception:
                                pass
                yield span
                span.set_status(_trace.Status(_trace.StatusCode.OK))
            except Exception as exc:
                try:
                    span.record_exception(exc)
                    span.set_status(
                        _trace.Status(_trace.StatusCode.ERROR, str(exc)[:300])
                    )
                except Exception:
                    pass
                raise
    except Exception:
        # If anything goes wrong with the OTEL machinery itself, fall
        # back to a no-op so the caller never sees a telemetry-induced
        # exception. The original work still runs.
        yield None


def record_tool_attributes(span: Any, attributes: dict[str, Any]) -> None:
    """Set OTEL attributes on a span if it is non-None and OTEL is enabled.

    Convenience helper so callers do not need to write the `if span is
    not None` guard at every call site.
    """
    if span is None:
        return
    for key, value in (attributes or {}).items():
        try:
            span.set_attribute(key, value)
        except Exception:
            try:
                span.set_attribute(key, str(value)[:1000])
            except Exception:
                pass


def _reset_for_tests() -> None:
    """Test-only helper to reset the cached availability + tracer."""
    global _otel_available, _tracer
    _otel_available = None
    _tracer = None
