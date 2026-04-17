from __future__ import annotations

"""Protocol-discipline settings.

v6.0.0 removed the user-facing strictness toggle. v6.0.1 layers an
Electron-class escape hatch on top:

- Interactive contexts run ``strict``. Interactive is defined as either
  of two signals:
    * both stdin and stdout are attached to a TTY (terminal users), OR
    * the client set ``NEXO_INTERACTIVE=1`` in the child process env
      (Electron clients like NEXO Desktop spawn ``claude`` through
      pipes, so ``isatty()`` returns False even with a human in the
      loop).
- Everything else (crons, tests, piped scripts, headless automation)
  runs ``lenient``.

``NEXO_INTERACTIVE`` is a contract between Brain and its interactive
clients. It is NOT user-facing. It is NOT documented to operators. It
is NOT the removed ``NEXO_PROTOCOL_STRICTNESS`` knob — that one let a
user force a strictness value, which confused people. This one only
signals interactivity; the actual strictness still follows the
TTY/interactive test above.

``VALID_PROTOCOL_STRICTNESS`` still exposes ``learning`` for internal
use by self-audit and onboarding flows, but nothing in this module
ever selects it.
"""

import os
import sys


DEFAULT_PROTOCOL_STRICTNESS = "strict"
VALID_PROTOCOL_STRICTNESS = {"lenient", "strict", "learning"}

# The only accepted value is the exact string "1". Truthy-looking values
# such as "true", "yes", "on" are deliberately ignored so a typo cannot
# silently re-enable strict mode on a headless machine.
_NEXO_INTERACTIVE_OPT_IN = "1"


def _is_interactive() -> bool:
    """True when the process should be treated as interactive.

    Two signals are accepted (OR semantics):
      1. stdin and stdout are both TTYs.
      2. ``NEXO_INTERACTIVE`` is exactly ``"1"`` — the Brain↔Electron
         contract used by NEXO Desktop ≥0.12.0.
    Anything else returns False, falling through to ``lenient``.
    """
    if os.environ.get("NEXO_INTERACTIVE") == _NEXO_INTERACTIVE_OPT_IN:
        return True
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


# Kept as a thin alias for any v6.0.0 caller that imported the old helper
# directly. New code should prefer ``_is_interactive()``.
def _stdio_is_tty() -> bool:
    """Deprecated in v6.0.1. Delegates to ``_is_interactive()`` so the
    NEXO_INTERACTIVE contract applies regardless of which name the caller
    imported."""
    return _is_interactive()


def normalize_protocol_strictness(value: str | None) -> str:
    """Coerce an arbitrary input into one of the canonical values.

    Unknown or empty values fall through to the interactivity test. The
    only normalisation is lowercasing and whitespace stripping — the
    v5.x alias table (default/normal/off/warn/soft) is gone.
    """
    candidate = str(value or "").strip().lower()
    if candidate in VALID_PROTOCOL_STRICTNESS:
        return candidate
    return "strict" if _is_interactive() else "lenient"


def get_protocol_strictness() -> str:
    """Return the active strictness for this process.

    No configuration, no user-facing environment, no calibration value —
    only the process context and the Brain↔client contract decide.
    """
    return "strict" if _is_interactive() else "lenient"
