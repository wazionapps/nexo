from __future__ import annotations

import importlib
import os
import sys

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture(autouse=True)
def _isolated(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    for mod in ["enforcement_engine", "guardian_config", "session_end_intent"]:
        importlib.reload(importlib.import_module(mod))
    yield


def _enforcer():
    from enforcement_engine import HeadlessEnforcer
    return HeadlessEnforcer()


def test_session_end_intent_empty_false():
    from session_end_intent import detect_session_end_intent
    assert detect_session_end_intent("") is False
    assert detect_session_end_intent(None) is False


def test_session_end_intent_classifier_yes_no():
    from session_end_intent import detect_session_end_intent
    assert detect_session_end_intent("lo dejamos aqui, seguimos mañana", classifier=lambda **kw: True) is True
    assert detect_session_end_intent("sigue con el siguiente bloque", classifier=lambda **kw: False) is False


def test_session_end_intent_fail_closed():
    from session_end_intent import detect_session_end_intent

    def boom(**_kw):
        raise RuntimeError("net")

    assert detect_session_end_intent("hasta mañana", classifier=boom) is False


def test_headless_enforcer_enqueues_end_prompts_on_implicit_close():
    enforcer = _enforcer()
    enforcer.on_user_message(
        "hasta mañana, lo dejamos por hoy",
        correction_detector=lambda _: False,
        session_end_detector=lambda _: True,
    )
    end_hits = [q for q in enforcer.injection_queue if q["tag"].startswith("session-end-intent:")]
    assert end_hits
    assert all("This session is ending" in q["prompt"] for q in end_hits)
    assert len(enforcer.injection_queue) == len(end_hits)


def test_headless_enforcer_keeps_normal_flow_when_session_stays_active():
    enforcer = _enforcer()
    enforcer.on_user_message(
        "sigue con el siguiente bloque y luego revisa tests",
        correction_detector=lambda _: False,
        session_end_detector=lambda _: False,
    )
    end_hits = [q for q in enforcer.injection_queue if q["tag"].startswith("session-end-intent:")]
    assert end_hits == []
