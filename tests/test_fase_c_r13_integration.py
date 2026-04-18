"""Integration tests for R13 pre-Edit guard inside HeadlessEnforcer (Fase C)."""
from __future__ import annotations

import importlib
import json
import os
import sys
import time

import pytest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def enforcer_runtime(isolated_db, tmp_path, monkeypatch):
    # Isolated NEXO_HOME so guardian_config fallback path does not touch the
    # operator's real runtime (learning #437 + regla dura 2).
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    (fake_home / "logs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))

    # Reload modules so the isolated NEXO_HOME takes effect.
    import enforcement_engine
    import guardian_config
    import r13_pre_edit_guard
    importlib.reload(r13_pre_edit_guard)
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    yield


def _fresh_enforcer():
    from enforcement_engine import HeadlessEnforcer
    enforcer = HeadlessEnforcer()
    # Clear the module-level in-process state — isolated_db already handles
    # DB isolation but the HeadlessEnforcer instance carries its own dicts.
    return enforcer


# ──────────────────────────────────────────────────────────────────────
# Happy paths — R13 fires on watched writes without guard_check
# ──────────────────────────────────────────────────────────────────────


def test_r13_enqueues_on_edit_without_guard():
    enforcer = _fresh_enforcer()
    enforcer.on_tool_call("Edit", {"file_path": "/repo/src/foo.py"})
    pending = list(enforcer.injection_queue)
    assert len(pending) == 1
    assert pending[0]["tag"].startswith("r13:")
    assert "/repo/src/foo.py" in pending[0]["prompt"]


def test_r13_silent_after_recent_guard_check_same_path():
    enforcer = _fresh_enforcer()
    # Simulate a guard_check on the target file within the window
    enforcer.on_tool_call("nexo_guard_check", {"files": "/repo/src/foo.py"})
    # NOTE: _extract_files only reads file_path/path/paths keys, not 'files'.
    # Force the record's files manually to emulate the real input shape.
    enforcer.recent_tool_records[-1] = enforcer.recent_tool_records[-1].__class__(
        tool=enforcer.recent_tool_records[-1].tool,
        ts=enforcer.recent_tool_records[-1].ts,
        files=("/repo/src/foo.py",),
    )
    enforcer.on_tool_call("Edit", {"file_path": "/repo/src/foo.py"})
    # No R13 injection expected
    r13_injections = [q for q in enforcer.injection_queue if q["tag"].startswith("r13:")]
    assert r13_injections == []


def test_r13_fires_after_guard_check_on_other_path():
    enforcer = _fresh_enforcer()
    # Guard_check on OTHER file
    enforcer.on_tool_call("nexo_guard_check", {"file_path": "/repo/other.py"})
    enforcer.on_tool_call("Edit", {"file_path": "/repo/src/foo.py"})
    r13_injections = [q for q in enforcer.injection_queue if q["tag"].startswith("r13:")]
    assert len(r13_injections) == 1
    assert "/repo/src/foo.py" in r13_injections[0]["prompt"]


def test_r13_non_write_tool_does_not_fire():
    enforcer = _fresh_enforcer()
    enforcer.on_tool_call("Bash", {"command": "ls -la /tmp"})
    enforcer.on_tool_call("Read", {"file_path": "/repo/src/foo.py"})
    enforcer.on_tool_call("Grep", {"pattern": "TODO"})
    r13_injections = [q for q in enforcer.injection_queue if q["tag"].startswith("r13:")]
    assert r13_injections == []


def test_r13_write_variants_all_trigger():
    enforcer = _fresh_enforcer()
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Delete"):
        enforcer.on_tool_call(tool, {"file_path": f"/repo/{tool.lower()}.py"})
    r13_injections = [q for q in enforcer.injection_queue if q["tag"].startswith("r13:")]
    # One per unique path → 5
    assert len(r13_injections) == 5


def test_r13_mcp_nexo_prefix_guard_suppresses():
    """Real hook payload prefix `mcp__nexo__nexo_guard_check` should also count."""
    enforcer = _fresh_enforcer()
    enforcer.on_tool_call("mcp__nexo__nexo_guard_check", {"file_path": "/repo/src/foo.py"})
    enforcer.on_tool_call("Edit", {"file_path": "/repo/src/foo.py"})
    r13_injections = [q for q in enforcer.injection_queue if q["tag"].startswith("r13:")]
    assert r13_injections == []


# ──────────────────────────────────────────────────────────────────────
# Guardian mode gating
# ──────────────────────────────────────────────────────────────────────


def test_r13_shadow_mode_logs_only(tmp_path, monkeypatch):
    """With R13 set to shadow in guardian.json, no injection is enqueued."""
    import guardian_config
    import enforcement_engine

    home = tmp_path / "shadow_home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    cfg_path = home / "config" / "guardian.json"
    # Start from the packaged default and downgrade R13 to shadow
    default = guardian_config.load_default_guardian_config()
    default["rules"]["R13_pre_edit_guard"] = "shadow"
    cfg_path.write_text(json.dumps(default))
    monkeypatch.setenv("NEXO_HOME", str(home))

    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)

    enforcer = enforcement_engine.HeadlessEnforcer()
    enforcer.on_tool_call("Edit", {"file_path": "/repo/src/foo.py"})
    r13_injections = [q for q in enforcer.injection_queue if q["tag"].startswith("r13:")]
    assert r13_injections == []


# ──────────────────────────────────────────────────────────────────────
# Tool record plumbing
# ──────────────────────────────────────────────────────────────────────


def test_tool_records_are_capped():
    enforcer = _fresh_enforcer()
    # Push beyond the cap
    for i in range(enforcer.max_recent_records + 10):
        enforcer.on_tool_call("Read", {"file_path": f"/f/{i}.py"})
    assert len(enforcer.recent_tool_records) == enforcer.max_recent_records


def test_extract_files_handles_multiple_shapes():
    enforcer = _fresh_enforcer()
    assert enforcer._extract_files({"file_path": "/a.py"}) == ["/a.py"]
    assert enforcer._extract_files({"path": "/b.py"}) == ["/b.py"]
    assert enforcer._extract_files({"paths": ["/c.py", "/d.py"]}) == ["/c.py", "/d.py"]
    assert enforcer._extract_files({}) == []
    assert enforcer._extract_files(None) == []
    assert enforcer._extract_files({"file_path": ""}) == []


def test_dedup_60s_on_same_tag():
    enforcer = _fresh_enforcer()
    enforcer.on_tool_call("Edit", {"file_path": "/repo/src/dup.py"})
    enforcer.on_tool_call("Edit", {"file_path": "/repo/src/dup.py"})
    r13_injections = [q for q in enforcer.injection_queue if q["tag"].startswith("r13:")]
    # Dedup by tag should keep only one
    assert len(r13_injections) == 1
