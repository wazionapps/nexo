"""Integration tests for R14 post-correction learning inside HeadlessEnforcer."""
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
def r14_runtime(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    (fake_home / "logs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    import enforcement_engine
    import guardian_config
    import r14_correction_learning
    importlib.reload(r14_correction_learning)
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    yield


def _enforcer():
    from enforcement_engine import HeadlessEnforcer
    return HeadlessEnforcer()


# ──────────────────────────────────────────────────────────────────────
# Decision module
# ──────────────────────────────────────────────────────────────────────


def test_detect_correction_empty_text_false():
    from r14_correction_learning import detect_correction
    assert detect_correction("") is False
    assert detect_correction(None) is False
    assert detect_correction("   ") is False


def test_detect_correction_too_short_false():
    """Single-word messages never route through the classifier."""
    from r14_correction_learning import detect_correction
    called = []

    def probe(**kw):
        called.append(kw)
        return True

    assert detect_correction("ok", classifier=probe) is False
    assert called == []  # classifier not invoked


def test_detect_correction_classifier_yes():
    from r14_correction_learning import detect_correction

    def yes(**kw):
        return True

    assert detect_correction("no es así, te equivocas", classifier=yes) is True


def test_detect_correction_fail_closed_on_exception():
    from r14_correction_learning import detect_correction

    def boom(**kw):
        raise RuntimeError("backend")

    assert detect_correction("long enough message for the classifier path", classifier=boom) is False


# ──────────────────────────────────────────────────────────────────────
# HeadlessEnforcer integration
# ──────────────────────────────────────────────────────────────────────


def test_r14_injects_after_three_tool_calls_without_learning():
    enforcer = _enforcer()
    enforcer.on_user_message("that is wrong, the opposite is true",
                             correction_detector=lambda _: True)
    # 3 tool calls, none is nexo_learning_add
    for tool in ("Read", "Grep", "Bash"):
        enforcer.on_tool_call(tool, {"file_path": "/x"})
    r14_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r14:")]
    assert len(r14_q) == 1
    assert "R14 post-user-correction" in r14_q[0]["prompt"]


def test_r14_suppressed_when_learning_add_inside_window():
    enforcer = _enforcer()
    enforcer.on_user_message("that is wrong", correction_detector=lambda _: True)
    enforcer.on_tool_call("Read", {"file_path": "/x"})
    enforcer.on_tool_call("nexo_learning_add", {"title": "captured", "content": "y"})
    enforcer.on_tool_call("Bash", {"command": "ls"})
    r14_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r14:")]
    assert r14_q == []


def test_r14_quiet_when_no_correction():
    enforcer = _enforcer()
    enforcer.on_user_message("thanks for the update", correction_detector=lambda _: False)
    for tool in ("Read", "Grep", "Bash"):
        enforcer.on_tool_call(tool, {})
    r14_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r14:")]
    assert r14_q == []


def test_r14_mcp_nexo_prefix_counts_as_learning_add():
    """The mcp__nexo__ prefix form must also close the window."""
    enforcer = _enforcer()
    enforcer.on_user_message("incorrect answer, redo", correction_detector=lambda _: True)
    enforcer.on_tool_call("Read", {})
    enforcer.on_tool_call("mcp__nexo__nexo_learning_add", {"title": "x", "content": "y"})
    enforcer.on_tool_call("Bash", {})
    r14_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r14:")]
    assert r14_q == []


def test_r14_fires_only_once_per_turn():
    enforcer = _enforcer()
    enforcer.on_user_message("wrong wrong wrong", correction_detector=lambda _: True)
    for _ in range(6):
        enforcer.on_tool_call("Read", {})
    r14_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r14:")]
    assert len(r14_q) == 1


def test_r14_shadow_mode_logs_only(tmp_path, monkeypatch):
    import json
    import guardian_config
    import enforcement_engine

    home = tmp_path / "shadow_home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    default = guardian_config.load_default_guardian_config()
    default["rules"]["R14_correction_learning"] = "shadow"
    (home / "config" / "guardian.json").write_text(json.dumps(default))
    monkeypatch.setenv("NEXO_HOME", str(home))

    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)

    enforcer = enforcement_engine.HeadlessEnforcer()
    enforcer.on_user_message("te equivocaste", correction_detector=lambda _: True)
    for _ in range(3):
        enforcer.on_tool_call("Read", {})
    r14_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r14:")]
    assert r14_q == []
