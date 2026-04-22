"""Idempotency + contract tests for v7.4.0 lifecycle events."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def _lifecycle_runtime(isolated_db):
    # isolated_db from tests/conftest.py already points NEXO_HOME at a
    # tmp dir with a fresh DB; migrations have run. No extra wiring.
    yield


def _call(**kwargs):
    from plugins.lifecycle_events import handle_nexo_lifecycle_event
    return json.loads(handle_nexo_lifecycle_event(**kwargs))


def test_first_delivery_returns_processed():
    res = _call(
        event_id="evt-1",
        action="archive",
        conversation_id="conv-1",
        session_id="sess-1",
        payload_snapshot=json.dumps({"title": "Alpha"}),
    )
    assert res["status"] == "processed"
    assert res["event_id"] == "evt-1"
    assert res["diary_triggered"] is True  # archive triggers diary
    assert res["duplicate"] is False


def test_duplicate_delivery_returns_already_processed_without_side_effects():
    _call(event_id="evt-dup", action="archive", conversation_id="c")
    # Same id, same action — must be a no-op
    res2 = _call(event_id="evt-dup", action="archive", conversation_id="c")
    assert res2["status"] == "already_processed"
    assert res2["duplicate"] is True
    # Even with a different action payload, the original row wins:
    # the event_id is the canonical idempotency key.
    res3 = _call(event_id="evt-dup", action="delete", conversation_id="c-other")
    assert res3["status"] == "already_processed"


def test_switch_action_does_not_trigger_diary():
    res = _call(event_id="evt-switch", action="switch", conversation_id="c")
    assert res["status"] == "processed"
    assert res["diary_triggered"] is False


def test_malformed_action_rejected():
    res = _call(event_id="evt-bad", action="not-a-real-action", conversation_id="c")
    assert res["status"] == "rejected"
    assert "unknown-action" in res["reason"]


def test_missing_event_id_rejected():
    res = _call(event_id="", action="archive", conversation_id="c")
    assert res["status"] == "rejected"
    assert "missing-event-id" in res["reason"]


def test_missing_conversation_id_rejected():
    res = _call(event_id="evt-no-conv", action="archive", conversation_id="")
    assert res["status"] == "rejected"
    assert "missing-conversation-id" in res["reason"]


def test_payload_snapshot_roundtrips_via_status_endpoint():
    _call(
        event_id="evt-round",
        action="close",
        conversation_id="c-round",
        payload_snapshot=json.dumps({"title": "Roundtrip", "is_active": True}),
    )
    from plugins.lifecycle_events import handle_nexo_lifecycle_status
    row = json.loads(handle_nexo_lifecycle_status("evt-round"))
    assert row["delivery_status"] == "processed"
    assert row["payload_snapshot"]["title"] == "Roundtrip"
    assert row["payload_snapshot"]["is_active"] is True


def test_status_endpoint_returns_not_found_for_unknown_id():
    from plugins.lifecycle_events import handle_nexo_lifecycle_status
    row = json.loads(handle_nexo_lifecycle_status("evt-does-not-exist"))
    assert row["status"] == "not_found"


def test_malformed_payload_snapshot_is_stored_as_raw_fallback():
    res = _call(
        event_id="evt-raw",
        action="archive",
        conversation_id="c-raw",
        payload_snapshot="not-valid-json{",
    )
    assert res["status"] == "processed"
    from plugins.lifecycle_events import handle_nexo_lifecycle_status
    row = json.loads(handle_nexo_lifecycle_status("evt-raw"))
    assert row["payload_snapshot"] == {"_raw": "not-valid-json{"}


def test_handler_exception_returns_retryable_error(monkeypatch):
    from plugins import lifecycle_events as plugin
    import lifecycle_events

    def boom(**kwargs):
        raise RuntimeError("db gone")
    monkeypatch.setattr(lifecycle_events, "record_lifecycle_event", boom)
    res = _call(event_id="evt-boom", action="archive", conversation_id="c")
    assert res["status"] == "retryable_error"
    assert "db gone" in res["reason"]
    assert res.get("handler_threw") is True


def test_all_diary_triggering_actions_flag_correctly():
    for i, action in enumerate(["close", "delete", "archive", "app-exit"]):
        res = _call(event_id=f"evt-diary-{i}", action=action, conversation_id=f"c-{i}")
        assert res["status"] == "processed"
        assert res["diary_triggered"] is True, f"{action} must trigger diary"
    # window-close is NOT in the diary-triggering set — Desktop runs the
    # window close overlay locally and the diary happens per-conv via
    # app-exit instead. This is a deliberate design choice; if the plan
    # ever shifts to put diary on window-close, flip the constant in
    # src/lifecycle_events.py and this test moves too.
    res = _call(event_id="evt-wc", action="window-close", conversation_id="c-wc")
    assert res["status"] == "processed"
    assert res["diary_triggered"] is False


def test_valid_actions_shape_is_frozen():
    import lifecycle_events
    assert lifecycle_events.VALID_ACTIONS == {
        "close",
        "delete",
        "archive",
        "switch",
        "app-exit",
        "window-close",
    }
