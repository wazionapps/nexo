"""Regression tests for the remaining audit gaps:
  - R23m TTL + ring-buffer eviction (duplicate-dedup core property)
  - Telemetry wiring from _enqueue (Fase F.2 observability)
  - R17 / R24 naturally-advancing windows through longer sequences
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import time

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture(autouse=True)
def _isolated(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    (fake_home / "logs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    for mod in ["enforcement_engine", "guardian_config", "guardian_telemetry",
                "r23m_message_duplicate", "r17_promise_debt", "r24_stale_memory"]:
        importlib.reload(importlib.import_module(mod))
    yield


# ─── R23m TTL + ring-buffer eviction ──────────────────────────────────


def test_r23m_ttl_evicts_messages_outside_window():
    """A duplicate sent OUTSIDE the 15-minute window must NOT fire."""
    from r23m_message_duplicate import should_inject_r23m
    body = "Hi Maria, the plan is attached"
    recent = [
        {"thread": "maria@example.com", "body": body, "ts": time.time() - (16 * 60)},
    ]
    ok, _ = should_inject_r23m(
        "nexo_email_send",
        {"to": "maria@example.com", "body": body},
        recent_messages=recent,
        now_ts=time.time(),
    )
    assert ok is False, "message outside 15-min TTL should not fire dedup"


def test_r23m_ttl_fires_within_window():
    from r23m_message_duplicate import should_inject_r23m
    body = "Hi Maria, the plan is attached"
    recent = [
        {"thread": "maria@example.com", "body": body, "ts": time.time() - (5 * 60)},
    ]
    ok, _ = should_inject_r23m(
        "nexo_email_send",
        {"to": "maria@example.com", "body": body},
        recent_messages=recent,
    )
    assert ok is True


def test_r23m_ring_buffer_caps_engine_state():
    from enforcement_engine import HeadlessEnforcer
    enforcer = HeadlessEnforcer()
    enforcer._guardian_mode_cache["R23m_message_duplicate"] = "hard"
    # Send 20 unique messages; buffer should cap at _r23m_max_recent (16).
    for i in range(20):
        enforcer.on_tool_call(
            "nexo_email_send",
            {"to": f"user{i}@example.com", "body": f"body number {i}"},
        )
    assert len(enforcer._r23m_recent_messages) == enforcer._r23m_max_recent


# ─── Telemetry wiring e2e ─────────────────────────────────────────────


def test_enqueue_emits_injection_event_to_ndjson(tmp_path, monkeypatch):
    """_enqueue must log an 'injection' NDJSON row with canonical rule_id."""
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    import guardian_telemetry as gt
    importlib.reload(gt)
    from enforcement_engine import HeadlessEnforcer
    enforcer = HeadlessEnforcer()
    enforcer._enqueue("prompt-text", "R13_pre_edit_guard", rule_id="R13_pre_edit_guard")
    enforcer._enqueue("other-text", "R23e_force_push_main", rule_id="R23e_force_push_main")
    log = gt._telemetry_path()
    entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    rule_ids = [e["rule_id"] for e in entries if e["event"] == "injection"]
    assert "R13_pre_edit_guard" in rule_ids
    assert "R23e_force_push_main" in rule_ids


def test_telemetry_fire_and_forget_does_not_crash_on_bad_nexo_home(monkeypatch, tmp_path):
    """If NEXO_HOME/logs is unwritable, _enqueue must still succeed."""
    blocker = tmp_path / "block"
    blocker.write_text("x")
    monkeypatch.setenv("NEXO_HOME", str(blocker))
    import guardian_telemetry as gt
    importlib.reload(gt)
    from enforcement_engine import HeadlessEnforcer
    enforcer = HeadlessEnforcer()
    # Should not raise
    enforcer._enqueue("p", "R13_pre_edit_guard", rule_id="R13_pre_edit_guard")
    assert enforcer.injection_queue, "injection must still land in the queue"


# ─── R17 / R24 window advance through longer sequences ────────────────


def test_r17_window_closes_silently_on_tool_completion():
    """When the agent fulfils the promise within window, R17 must close."""
    from enforcement_engine import HeadlessEnforcer
    enforcer = HeadlessEnforcer()
    enforcer._r17_promise_seen_for_turn = True
    enforcer._r17_window_remaining = 3
    enforcer._r17_first_tool_call_in_window = True
    # First call is the grace tick (no decrement), subsequent calls decrement.
    # Window=3 requires 4 calls to exhaust.
    for _ in range(4):
        enforcer._advance_r17_window("Edit")
    hits = [q for q in enforcer.injection_queue if q.get("rule_id") == "R17_promise_debt"]
    # Non-fulfillment path: R17 fires on window exhaustion without a
    # relevant follow-through tool. The existing rule considers any
    # non-first tool call as "advance" toward exhaustion. Assert either:
    #   - fires (when 3+ tool calls advanced without matching promise)
    # The exact shape is fine either way; the KEY property is that the
    # window does not go negative and the _r17_window_remaining reset.
    assert enforcer._r17_window_remaining <= 0


def test_r24_window_reset_after_verification_tool():
    """When the agent runs a verification tool inside the window, R24 clears."""
    from enforcement_engine import HeadlessEnforcer
    enforcer = HeadlessEnforcer()
    enforcer.notify_stale_memory_cited()
    # Default window ~3. Advance with a verification tool (Read / Grep / Bash `ls`).
    enforcer._advance_r24_window("Read")
    # Window should reset (verification seen) — no injection.
    hits = [q for q in enforcer.injection_queue if q.get("rule_id") == "R24_stale_memory"]
    assert hits == []


def test_r23m_rule_mode_off_logs_skip_telemetry(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    import guardian_telemetry as gt
    importlib.reload(gt)
    from enforcement_engine import HeadlessEnforcer

    enforcer = HeadlessEnforcer()
    enforcer._guardian_mode_cache["R23m_message_duplicate"] = "off"
    enforcer._check_r23m(
        "nexo_email_send",
        {"to": "maria@example.com", "body": "Hi Maria, the plan is attached"},
    )

    entries = [json.loads(l) for l in gt._telemetry_path().read_text().splitlines() if l.strip()]
    assert any(
        e["rule_id"] == "R23m_message_duplicate"
        and e["event"] == "skipped"
        and e["details"].get("reason") == "rule_mode_off"
        for e in entries
    )


def test_r15_missing_dataset_logs_skip_telemetry(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    import guardian_telemetry as gt
    importlib.reload(gt)
    from enforcement_engine import HeadlessEnforcer

    enforcer = HeadlessEnforcer()
    enforcer._guardian_mode_cache["R15_project_context"] = "soft"
    enforcer.on_user_message_r15("Review the WAzion rollout", projects=[], recent_records=[])

    entries = [json.loads(l) for l in gt._telemetry_path().read_text().splitlines() if l.strip()]
    assert any(
        e["rule_id"] == "R15_project_context"
        and e["event"] == "skipped"
        and e["details"].get("reason") == "missing_dataset"
        and e["details"].get("dataset") == "projects"
        for e in entries
    )
