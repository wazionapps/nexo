from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "src" / "scripts"

for path in (str(REPO_ROOT / "src"), str(SCRIPT_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)


import nexo_personal_automation as automation


def test_run_personal_automation_text_uses_safe_defaults_and_infers_caller(monkeypatch, tmp_path):
    monkeypatch.setattr(automation, "NEXO_HOME", tmp_path / "nexo")
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "personal" / "scripts" / "reviews-watch.py")])
    captured: dict = {}

    def _fake_run(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(automation, "_run_automation_text", _fake_run)

    result = automation.run_personal_automation_text("hola")

    assert result == "ok"
    assert captured["model"] == ""
    assert captured["timeout"] == automation.DEFAULT_SHORT_TEXT_TIMEOUT
    assert captured["allowed_tools"] == ""
    assert captured["include_bootstrap"] is False
    assert captured["bare_mode"] is True
    assert captured["caller"] == "personal/reviews-watch"


def test_personal_automation_lock_rejects_overlapping_live_pid(monkeypatch, tmp_path):
    monkeypatch.setattr(automation, "NEXO_HOME", tmp_path / "nexo")
    monkeypatch.setattr(automation, "_pid_is_alive", lambda pid: True)
    lock_path = automation._caller_lock_path("personal/reviews-watch")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("99999\n0\npersonal/reviews-watch\n")

    with pytest.raises(RuntimeError):
        automation._acquire_personal_caller_lock("personal/reviews-watch")
