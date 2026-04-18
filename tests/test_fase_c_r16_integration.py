"""Integration tests for R16 declared-done guard in HeadlessEnforcer."""
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
def r16_runtime(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    import enforcement_engine
    import guardian_config
    import r16_declared_done
    importlib.reload(r16_declared_done)
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    yield


def _enforcer():
    from enforcement_engine import HeadlessEnforcer
    return HeadlessEnforcer()


# ──────────────────────────────────────────────────────────────────────
# Decision module
# ──────────────────────────────────────────────────────────────────────


def test_r16_decision_empty_false():
    from r16_declared_done import detect_declared_done
    assert detect_declared_done("") is False
    assert detect_declared_done(None) is False


def test_r16_decision_too_short_false():
    from r16_declared_done import detect_declared_done
    called = []
    def probe(**kw):
        called.append(kw)
        return True
    assert detect_declared_done("done", classifier=probe) is False
    assert detect_declared_done("ok.", classifier=probe) is False
    assert called == []


def test_r16_decision_fail_closed():
    from r16_declared_done import detect_declared_done
    def boom(**kw):
        raise RuntimeError("net")
    assert detect_declared_done("task complete, all changes applied", classifier=boom) is False


def test_r16_decision_yes_no():
    from r16_declared_done import detect_declared_done
    assert detect_declared_done("The migration is finished", classifier=lambda **kw: True) is True
    assert detect_declared_done("Still working on step 3", classifier=lambda **kw: False) is False


# ──────────────────────────────────────────────────────────────────────
# HeadlessEnforcer integration
# ──────────────────────────────────────────────────────────────────────


def test_r16_injects_when_declared_and_open_task():
    enforcer = _enforcer()
    enforcer.on_assistant_text(
        "I have finished the full refactor",
        declared_detector=lambda _: True,
        has_open_task=lambda: True,
    )
    r16_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r16:")]
    assert len(r16_q) == 1
    assert "R16" in r16_q[0]["prompt"]
    assert "task_close" in r16_q[0]["prompt"]


def test_r16_quiet_when_no_open_task():
    enforcer = _enforcer()
    enforcer.on_assistant_text(
        "Task is done and shipped",
        declared_detector=lambda _: True,
        has_open_task=lambda: False,
    )
    r16_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r16:")]
    assert r16_q == []


def test_r16_quiet_when_not_declared():
    enforcer = _enforcer()
    enforcer.on_assistant_text(
        "Still looking into the bug, two hypotheses pending",
        declared_detector=lambda _: False,
        has_open_task=lambda: True,
    )
    r16_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r16:")]
    assert r16_q == []


def test_r16_dedup_within_turn():
    enforcer = _enforcer()
    for _ in range(3):
        enforcer.on_assistant_text(
            "Work is complete and ready",
            declared_detector=lambda _: True,
            has_open_task=lambda: True,
        )
    r16_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r16:")]
    # _enqueue dedups by tag; single injection
    assert len(r16_q) == 1


def test_r16_probe_failure_fails_closed():
    """When the open-task probe raises, we assume no open task (no injection)."""
    enforcer = _enforcer()
    def boom():
        raise RuntimeError("db down")
    enforcer.on_assistant_text(
        "The migration is complete",
        declared_detector=lambda _: True,
        has_open_task=boom,
    )
    r16_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r16:")]
    assert r16_q == []


def test_r16_shadow_mode_logs_only(tmp_path, monkeypatch):
    import json
    import guardian_config
    import enforcement_engine

    home = tmp_path / "shadow_home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    default = guardian_config.load_default_guardian_config()
    default["rules"]["R16_declared_done"] = "shadow"
    (home / "config" / "guardian.json").write_text(json.dumps(default))
    monkeypatch.setenv("NEXO_HOME", str(home))

    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)

    enforcer = enforcement_engine.HeadlessEnforcer()
    enforcer.on_assistant_text(
        "Done with the upgrade",
        declared_detector=lambda _: True,
        has_open_task=lambda: True,
    )
    r16_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r16:")]
    assert r16_q == []
