"""Integration tests for Fase D R17 promise-debt + R20 constant-change."""
from __future__ import annotations

import importlib
import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def fase_d_runtime(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    import enforcement_engine
    import guardian_config
    import r17_promise_debt
    import r20_constant_change
    importlib.reload(r17_promise_debt)
    importlib.reload(r20_constant_change)
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    yield


def _enforcer():
    from enforcement_engine import HeadlessEnforcer
    return HeadlessEnforcer()


# ──────────────────────────────────────────────────────────────────────
# R17 decision
# ──────────────────────────────────────────────────────────────────────


def test_r17_decision_short_text_skips_classifier():
    from r17_promise_debt import detect_promise
    seen = []
    def probe(**kw):
        seen.append(1)
        return True
    assert detect_promise("ok", classifier=probe) is False
    assert seen == []


def test_r17_decision_fail_closed():
    from r17_promise_debt import detect_promise
    def boom(**kw):
        raise RuntimeError("x")
    assert detect_promise("I will implement the feature later", classifier=boom) is False


def test_r17_decision_classifier_yes():
    from r17_promise_debt import detect_promise
    assert detect_promise("I will create the migration next turn", classifier=lambda **k: True) is True


# ──────────────────────────────────────────────────────────────────────
# R17 integration
# ──────────────────────────────────────────────────────────────────────


def test_r17_injects_when_promise_without_action_after_window():
    enforcer = _enforcer()
    enforcer.on_assistant_text_r17("I will implement the migration next turn",
                                    promise_detector=lambda _: True)
    # First tool call counts as "agent acting" but doesn't close
    # immediately per our MVP definition; two more required.
    enforcer.on_tool_call("Read", {})
    enforcer.on_tool_call("Grep", {})
    enforcer.on_tool_call("Bash", {})
    r17_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r17:")]
    # The window is 2 calls + the "first tool call" grace — so the third
    # tool call exhausts the window without a declaration of promise met.
    assert len(r17_q) == 1
    assert "R17 promise-debt" in r17_q[0]["prompt"]


def test_r17_quiet_without_promise():
    enforcer = _enforcer()
    enforcer.on_assistant_text_r17("Already done, tests green",
                                    promise_detector=lambda _: False)
    for _ in range(5):
        enforcer.on_tool_call("Read", {})
    r17_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r17:")]
    assert r17_q == []


# ──────────────────────────────────────────────────────────────────────
# R20 decision
# ──────────────────────────────────────────────────────────────────────


def test_r20_extract_candidate_symbols():
    from r20_constant_change import _extract_candidate_symbols
    assert "TIMEOUT" in _extract_candidate_symbols("TIMEOUT = 30")
    assert "api_endpoint" in _extract_candidate_symbols("self.api_endpoint = '/x'")
    # Too short ignored
    assert "x" not in _extract_candidate_symbols("x = 1")


def test_r20_classifier_no_skips_injection():
    from r20_constant_change import should_inject_r20
    r = should_inject_r20(
        "/x/local.py",
        "local_var = 5",
        [],
        classifier=lambda **k: False,
    )
    assert r is None


def test_r20_classifier_yes_no_grep_injects():
    from r20_constant_change import should_inject_r20
    r = should_inject_r20(
        "/x/config.py",
        "DEFAULT_TIMEOUT = 30",
        [],
        classifier=lambda **k: True,
    )
    assert r is not None
    assert r["tag"] == "r20:/x/config.py"


def test_r20_grep_in_history_suppresses():
    from r20_constant_change import should_inject_r20
    from r13_pre_edit_guard import ToolCallRecord
    records = [ToolCallRecord(tool="Grep", ts=1.0, files=("DEFAULT_TIMEOUT",))]
    r = should_inject_r20(
        "/x/config.py",
        "DEFAULT_TIMEOUT = 30",
        records,
        classifier=lambda **k: True,
    )
    assert r is None


# ──────────────────────────────────────────────────────────────────────
# R20 integration
# ──────────────────────────────────────────────────────────────────────


def test_r20_integration_detects_on_edit(monkeypatch):
    import r20_constant_change
    # Force classifier yes
    monkeypatch.setattr(r20_constant_change, "classify_edit_is_constant_change",
                        lambda file_path, new_string, classifier=None: True)
    enforcer = _enforcer()
    enforcer.on_tool_call("Edit", {
        "file_path": "/repo/config.py",
        "new_string": "DEFAULT_TIMEOUT = 30",
    })
    r20_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r20:")]
    assert len(r20_q) == 1


def test_r20_integration_quiet_when_classifier_no(monkeypatch):
    import r20_constant_change
    monkeypatch.setattr(r20_constant_change, "classify_edit_is_constant_change",
                        lambda file_path, new_string, classifier=None: False)
    enforcer = _enforcer()
    enforcer.on_tool_call("Edit", {
        "file_path": "/repo/local.py",
        "new_string": "local_var = 5",
    })
    r20_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r20:")]
    assert r20_q == []
