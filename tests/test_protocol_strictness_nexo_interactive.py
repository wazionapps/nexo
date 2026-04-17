"""v6.0.1 — Protocol strictness respects NEXO_INTERACTIVE=1 override.

Verifies the Brain↔Electron contract: an Electron client (NEXO Desktop
0.12.0) spawns ``claude`` through pipes, so both stdin and stdout look
non-TTY, yet the human is in the loop and the session must run
``strict``. The override only accepts the exact string ``"1"`` to keep
typo-driven false positives out of headless crons.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import protocol_settings as ps  # noqa: E402


class _FakeStream:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _force_tty(monkeypatch, value: bool) -> None:
    stream = _FakeStream(value)
    monkeypatch.setattr(sys, "stdin", stream)
    monkeypatch.setattr(sys, "stdout", stream)


def test_env_override_strict_without_tty(monkeypatch):
    _force_tty(monkeypatch, False)
    monkeypatch.setenv("NEXO_INTERACTIVE", "1")
    assert ps.get_protocol_strictness() == "strict"


def test_no_env_no_tty_falls_back_to_lenient(monkeypatch):
    _force_tty(monkeypatch, False)
    monkeypatch.delenv("NEXO_INTERACTIVE", raising=False)
    assert ps.get_protocol_strictness() == "lenient"


def test_tty_without_env_is_strict(monkeypatch):
    _force_tty(monkeypatch, True)
    monkeypatch.delenv("NEXO_INTERACTIVE", raising=False)
    assert ps.get_protocol_strictness() == "strict"


def test_env_override_only_accepts_literal_one(monkeypatch):
    _force_tty(monkeypatch, False)
    for bad_value in ("0", "true", "yes", "on", " 1", "1 ", ""):
        monkeypatch.setenv("NEXO_INTERACTIVE", bad_value)
        assert ps.get_protocol_strictness() == "lenient", (
            f"NEXO_INTERACTIVE={bad_value!r} should NOT enable strict"
        )


def test_normalize_falls_through_to_interactive_decision(monkeypatch):
    _force_tty(monkeypatch, False)
    monkeypatch.setenv("NEXO_INTERACTIVE", "1")
    # Unknown value coerces via the interactivity test → strict here.
    assert ps.normalize_protocol_strictness("garbage") == "strict"


def test_deprecated_helper_still_respects_contract(monkeypatch):
    _force_tty(monkeypatch, False)
    monkeypatch.setenv("NEXO_INTERACTIVE", "1")
    assert ps._stdio_is_tty() is True
