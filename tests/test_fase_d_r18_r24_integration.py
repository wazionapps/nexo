"""Integration tests for Fase D tranche 4 — R18 + R24 (closure of Fase D Python)."""
from __future__ import annotations

import importlib
import json
import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def fase_d4_runtime(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    import enforcement_engine
    import guardian_config
    import r18_followup_autocomplete
    import r24_stale_memory
    importlib.reload(r18_followup_autocomplete)
    importlib.reload(r24_stale_memory)
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    yield


def _enforcer():
    from enforcement_engine import HeadlessEnforcer
    return HeadlessEnforcer()


def _enforcer_with_mode(rule_id: str, mode: str, tmp_path):
    import guardian_config, enforcement_engine
    home = tmp_path / f"{rule_id}_home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    default = guardian_config.load_default_guardian_config()
    default["rules"][rule_id] = mode
    (home / "config" / "guardian.json").write_text(json.dumps(default))
    os.environ["NEXO_HOME"] = str(home)
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    return enforcement_engine.HeadlessEnforcer()


# ──────────────────────────────────────────────────────────────────────
# R18 decision
# ──────────────────────────────────────────────────────────────────────


def test_r18_quiet_on_non_closure_tool():
    from r18_followup_autocomplete import should_suggest_r18
    # Read is not a closure tool; R18 does not run regardless of matches.
    assert should_suggest_r18("Read", {"file_path": "/x"},
                              match_helper=lambda d, threshold=0.7: [{"id": "NF-1", "similarity": 0.9, "description": "x"}]) is None


def test_r18_fires_on_closure_tool_with_match():
    from r18_followup_autocomplete import should_suggest_r18
    match = [{"id": "NF-42", "similarity": 0.85, "description": "verify X"}]
    r = should_suggest_r18("nexo_task_close", {"outcome_notes": "verified X done"},
                            match_helper=lambda d, threshold=0.7: match)
    assert r is not None
    assert r["count"] == 1
    assert r["matches"][0]["id"] == "NF-42"


def test_r18_fail_closed_on_helper_error():
    from r18_followup_autocomplete import should_suggest_r18
    def boom(description, threshold=0.7):
        raise RuntimeError("db")
    assert should_suggest_r18("nexo_task_close", {"x": "y"}, match_helper=boom) is None


# ──────────────────────────────────────────────────────────────────────
# R18 integration
# ──────────────────────────────────────────────────────────────────────


def test_r18_integration_suggests_on_task_close(monkeypatch):
    import r18_followup_autocomplete
    monkeypatch.setattr(
        r18_followup_autocomplete,
        "should_suggest_r18",
        lambda tool_name, tool_input: {
            "tag": "r18:followup-autocomplete",
            "count": 1,
            "matches": [{"id": "NF-TEST", "similarity": 0.82, "description": "test followup"}],
        },
    )
    import enforcement_engine
    importlib.reload(enforcement_engine)
    enforcer = enforcement_engine.HeadlessEnforcer()
    enforcer.on_tool_call("nexo_task_close", {"outcome_notes": "done"})
    r18 = [q for q in enforcer.injection_queue if q["tag"].startswith("r18:")]
    assert len(r18) == 1
    assert "NF-TEST" in r18[0]["prompt"]


# ──────────────────────────────────────────────────────────────────────
# R24 decision
# ──────────────────────────────────────────────────────────────────────


def test_r24_is_verification_tool():
    from r24_stale_memory import is_verification_tool
    assert is_verification_tool("Grep") is True
    assert is_verification_tool("Read") is True
    assert is_verification_tool("nexo_entity_list") is True
    assert is_verification_tool("Edit") is False


def test_r24_logic():
    from r24_stale_memory import should_flag_r24
    assert should_flag_r24(True, False, True) is True
    assert should_flag_r24(True, True, True) is False   # verification happened
    assert should_flag_r24(False, False, True) is False
    assert should_flag_r24(True, False, False) is False  # window not yet exhausted


# ──────────────────────────────────────────────────────────────────────
# R24 integration
# ──────────────────────────────────────────────────────────────────────


def test_r24_injects_when_window_exhausted_without_verification(tmp_path):
    enforcer = _enforcer_with_mode("R24_stale_memory", "hard", tmp_path)
    enforcer.notify_stale_memory_cited()
    # 3 non-verification tool calls exhaust the window
    enforcer.on_tool_call("Edit", {"file_path": "/repo/foo.py"})
    enforcer.on_tool_call("Edit", {"file_path": "/repo/bar.py"})
    enforcer.on_tool_call("Write", {"file_path": "/repo/baz.py"})
    r24 = [q for q in enforcer.injection_queue if q["tag"].startswith("r24:")]
    assert len(r24) == 1
    assert "stale memory" in r24[0]["prompt"].lower()


def test_r24_silent_when_verification_happens(tmp_path):
    enforcer = _enforcer_with_mode("R24_stale_memory", "hard", tmp_path)
    enforcer.notify_stale_memory_cited()
    enforcer.on_tool_call("Grep", {"pattern": "foo"})  # verification
    enforcer.on_tool_call("Edit", {"file_path": "/x"})
    enforcer.on_tool_call("Edit", {"file_path": "/y"})
    r24 = [q for q in enforcer.injection_queue if q["tag"].startswith("r24:")]
    assert r24 == []


def test_r24_shadow_default_does_not_inject():
    """R24 ships shadow by default per plan doc 1."""
    enforcer = _enforcer()
    enforcer.notify_stale_memory_cited()
    for _ in range(4):
        enforcer.on_tool_call("Edit", {"file_path": "/x"})
    r24 = [q for q in enforcer.injection_queue if q["tag"].startswith("r24:")]
    assert r24 == []


def test_r24_silent_without_stale_memory():
    enforcer = _enforcer()
    for _ in range(5):
        enforcer.on_tool_call("Edit", {"file_path": "/x"})
    r24 = [q for q in enforcer.injection_queue if q["tag"].startswith("r24:")]
    assert r24 == []
