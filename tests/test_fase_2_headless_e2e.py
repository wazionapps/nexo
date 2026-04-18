"""End-to-end tests for the headless Protocol Enforcer.

Fase 2 audit revealed that `run_with_enforcement` never invoked
`enforcer.on_user_message(prompt)`, leaving R14 (CORE) and R15 dead in
the headless runtime. These tests pin that the fix stays in place and
that rule-id telemetry plus tag-only dedup behave as specified.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from unittest import mock

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture(autouse=True)
def _isolated(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    for mod in ["enforcement_engine", "guardian_config", "guardian_telemetry",
                "r14_correction_learning", "r15_project_context"]:
        importlib.reload(importlib.import_module(mod))
    yield


def test_on_user_message_updates_r15_and_r25_context_even_without_r14_module(monkeypatch):
    """R15/R25 context must fire independently of R14 import status.

    Before the fix, `on_user_message` short-circuited on R14 module
    absence and R15 / `_r25_last_user_text` never ran. That broke R15
    (project-context hint) and R25 (permit-marker detection).
    """
    from enforcement_engine import HeadlessEnforcer
    import enforcement_engine as eng

    monkeypatch.setattr(eng, "_detect_correction", None, raising=False)
    enforcer = HeadlessEnforcer()
    enforcer.on_user_message("please check the WAzion deploy")
    assert enforcer._r25_last_user_text == "please check the WAzion deploy"


def test_on_user_message_r14_off_still_updates_r25_text():
    """Even when R14 mode is `off`, R25 must still see the user text."""
    from enforcement_engine import HeadlessEnforcer
    enforcer = HeadlessEnforcer()
    enforcer._guardian_mode_cache["R14_correction_learning"] = "off"
    enforcer.on_user_message("borra eso por favor")
    assert enforcer._r25_last_user_text == "borra eso por favor"


def test_enqueue_tags_carry_canonical_rule_id_for_telemetry():
    """Telemetry aggregation requires every enqueue to carry `rule_id`."""
    from enforcement_engine import HeadlessEnforcer
    enforcer = HeadlessEnforcer()
    # Force-enqueue a rule; check queue entry shape
    enforcer._enqueue("prompt text", "R23e_force_push_main", rule_id="R23e_force_push_main")
    assert enforcer.injection_queue
    entry = enforcer.injection_queue[-1]
    assert entry["tag"] == "R23e_force_push_main"
    assert entry["rule_id"] == "R23e_force_push_main"


def test_enqueue_dedup_is_tag_only_for_capa2_rules():
    """Capa 2 tags (no legacy prefix) must dedup by exact tag — the
    old behaviour (parse tool out of tag) produced garbage for new rules.
    """
    from enforcement_engine import HeadlessEnforcer
    enforcer = HeadlessEnforcer()
    # Two distinct tags but same file path suffix — used to collide under
    # the old `tag.split(":")[-1]` tool extraction.
    enforcer._enqueue("prompt A", "r13:/repo/x.py", rule_id="R13_pre_edit_guard")
    enforcer._enqueue("prompt B", "r20:/repo/x.py", rule_id="R20_constant_change")
    tags = [q["tag"] for q in enforcer.injection_queue]
    assert "r13:/repo/x.py" in tags
    assert "r20:/repo/x.py" in tags
    assert len(enforcer.injection_queue) == 2


def test_enqueue_legacy_tag_keeps_time_dedup():
    """Legacy `after:X->Y` tags still use the 60s tool-based dedup."""
    import time as _time
    from enforcement_engine import HeadlessEnforcer
    enforcer = HeadlessEnforcer()
    enforcer.tools_called.add("nexo_learning_add")
    enforcer.tool_timestamps["nexo_learning_add"] = _time.time()
    # Legacy tag would have fired before, but because the tool was called
    # in the last 60s the legacy dedup path should short-circuit.
    enforcer._enqueue(
        "reminder",
        "after:nexo_task_close->nexo_learning_add",
        rule_id="after_tool_dependency",
    )
    assert not enforcer.injection_queue


def test_telemetry_receives_canonical_rule_id(tmp_path, monkeypatch):
    """The enqueue → telemetry log wire should carry the canonical rule_id
    regardless of the per-call tag shape (lowercase r13: vs PascalCase R23b).
    """
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    import guardian_telemetry as gt
    importlib.reload(gt)
    from enforcement_engine import HeadlessEnforcer
    enforcer = HeadlessEnforcer()
    enforcer._enqueue("p1", "r13:/foo/bar.py", rule_id="R13_pre_edit_guard")
    enforcer._enqueue("p2", "R23e_force_push_main", rule_id="R23e_force_push_main")
    entries = [json.loads(l) for l in gt._telemetry_path().read_text().splitlines() if l.strip()]
    rule_ids = [e["rule_id"] for e in entries if e["event"] == "injection"]
    assert "R13_pre_edit_guard" in rule_ids
    assert "R23e_force_push_main" in rule_ids


def test_run_with_enforcement_forwards_initial_prompt_to_on_user_message(monkeypatch):
    """Critical regression guard: the stream runner MUST forward the
    initial prompt to enforcer.on_user_message so R14/R15 run in
    headless mode. Before the fix, these rules silently stayed dead.
    """
    import enforcement_engine as eng

    sent = []

    class _FakeEnforcer:
        def __init__(self):
            self.injection_queue = []
            self._injections_done = 0
            self.tools_called = set()
            self.tool_call_count = 0
            self.map = {"version": "2.1.0", "tools": {}}

        def on_user_message(self, text, **kwargs):
            sent.append(text)

        def on_tool_call(self, *a, **kw):
            pass

        def on_assistant_text(self, *a, **kw):
            pass

        def on_assistant_text_r17(self, *a, **kw):
            pass

        def check_periodic(self):
            pass

        def flush(self):
            return None

        def get_end_prompts(self):
            return []

        def summary(self):
            return ""

    # Fake subprocess that emits a single `result` event and exits.
    class _FakeStdout:
        def __init__(self):
            self._lines = iter([json.dumps({"type": "result"}) + "\n"])

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._lines)

    class _FakeStderr:
        def __iter__(self):
            return iter([])

    class _FakeStdin:
        def write(self, _):
            pass

        def flush(self):
            pass

        def close(self):
            pass

    class _FakeProc:
        def __init__(self):
            self.stdin = _FakeStdin()
            self.stdout = _FakeStdout()
            self.stderr = _FakeStderr()
            self.returncode = 0

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(eng.subprocess, "Popen", lambda *a, **kw: _FakeProc())

    fake_enforcer = _FakeEnforcer()
    monkeypatch.setattr(eng, "HeadlessEnforcer", lambda: fake_enforcer)
    eng.run_with_enforcement(["claude"], prompt="please deploy WAzion", cwd="/tmp", timeout=5)
    assert sent == ["please deploy WAzion"]
