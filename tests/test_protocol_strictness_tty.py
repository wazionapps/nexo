"""v6.0.0 — Protocol strictness is decided by TTY detection, nothing else.

Verifies:
  - Interactive TTY → strict.
  - Non-TTY (cron, pipe, tests) → lenient.
  - ENV NEXO_PROTOCOL_STRICTNESS is ignored even when present (v5.x behaviour gone).
  - calibration.json preferences.protocol_strictness is ignored (v5.x gone).
  - normalize_protocol_strictness maps explicit known values, otherwise falls
    through to the TTY decision.
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

    def isatty(self) -> bool:  # pragma: no cover - trivial
        return self._tty


def _force_tty(monkeypatch, value: bool) -> None:
    stream = _FakeStream(value)
    monkeypatch.setattr(sys, "stdin", stream)
    monkeypatch.setattr(sys, "stdout", stream)


def test_tty_detection_returns_strict(monkeypatch):
    _force_tty(monkeypatch, True)
    assert ps.get_protocol_strictness() == "strict"


def test_non_tty_returns_lenient(monkeypatch):
    _force_tty(monkeypatch, False)
    assert ps.get_protocol_strictness() == "lenient"


def test_env_variable_has_no_effect(monkeypatch):
    monkeypatch.setenv("NEXO_PROTOCOL_STRICTNESS", "lenient")
    _force_tty(monkeypatch, True)
    assert ps.get_protocol_strictness() == "strict"


def test_calibration_file_has_no_effect(monkeypatch, tmp_path):
    # Simulate a v5.x calibration.json with an explicit protocol_strictness.
    brain = tmp_path / "brain"
    brain.mkdir()
    (brain / "calibration.json").write_text(
        '{"preferences": {"protocol_strictness": "lenient"}}'
    )
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    _force_tty(monkeypatch, True)
    assert ps.get_protocol_strictness() == "strict"


def test_normalize_accepts_canonical_values(monkeypatch):
    _force_tty(monkeypatch, False)  # baseline is lenient
    assert ps.normalize_protocol_strictness("strict") == "strict"
    assert ps.normalize_protocol_strictness("learning") == "learning"
    assert ps.normalize_protocol_strictness("lenient") == "lenient"


def test_normalize_drops_legacy_aliases(monkeypatch):
    # v5.x aliases (default/normal/off/warn/soft) no longer map — they must
    # fall through to the TTY decision instead.
    _force_tty(monkeypatch, True)
    for alias in ("default", "normal", "off", "warn", "soft"):
        assert ps.normalize_protocol_strictness(alias) == "strict"


def test_valid_set_still_contains_learning():
    assert "learning" in ps.VALID_PROTOCOL_STRICTNESS
    assert ps.VALID_PROTOCOL_STRICTNESS == {"strict", "lenient", "learning"}
