from __future__ import annotations

"""Protocol-discipline settings.

v6.0.0 breaking change: there is no user-facing toggle anymore.

- Interactive TTY sessions always run ``strict``.
- Non-TTY contexts (crons, tests, pipes) always run ``lenient`` — exactly
  what every scheduled background job needs to avoid noisy protocol nags.
- ``VALID_PROTOCOL_STRICTNESS`` still exposes ``learning`` for internal
  use by self-audit and onboarding flows, but it is never the active mode
  unless the code explicitly asks for it.

The v5.x surfaces this module used to expose — ``NEXO_PROTOCOL_STRICTNESS``
environment variable, ``preferences.protocol_strictness`` in calibration,
and the ``default/normal/off/warn/soft`` aliases — are all removed on
purpose. Users who relied on them see their value silently cleared by the
v6.0.0 calibration migration and fall through to the TTY/no-TTY decision.
"""

import sys


DEFAULT_PROTOCOL_STRICTNESS = "strict"
VALID_PROTOCOL_STRICTNESS = {"lenient", "strict", "learning"}


def _stdio_is_tty() -> bool:
    """True only when both stdin and stdout are attached to a terminal.

    The double-check matters: a headless cron typically redirects stdout
    to a log file but leaves stdin as a TTY. Treating that as interactive
    would re-enable strict mode for every cron invocation, which is
    exactly the noise v6.0.0 set out to eliminate.
    """
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


def normalize_protocol_strictness(value: str | None) -> str:
    """Coerce an arbitrary input into one of the canonical values.

    Unknown or empty values return the TTY-derived default. The only
    normalization done is lowercasing and whitespace stripping — the v5.x
    alias table is gone.
    """
    candidate = str(value or "").strip().lower()
    if candidate in VALID_PROTOCOL_STRICTNESS:
        return candidate
    return "strict" if _stdio_is_tty() else "lenient"


def get_protocol_strictness() -> str:
    """Return the active strictness for this process.

    No configuration, no environment, no calibration — only the process
    context decides. Callers that want to force a value for tests can
    monkeypatch ``sys.stdin.isatty`` / ``sys.stdout.isatty`` or call
    ``normalize_protocol_strictness`` directly with an explicit value.
    """
    return "strict" if _stdio_is_tty() else "lenient"
