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


def test_first_delivery_without_session_id_returns_processed():
    # v7.5 contract: if no session_id is present, Brain has no live
    # process to orchestrate against, so the answer remains the v7.4
    # ledger-only "processed".
    res = _call(
        event_id="evt-1",
        action="archive",
        conversation_id="conv-1",
        payload_snapshot=json.dumps({"title": "Alpha"}),
    )
    assert res["status"] == "processed"
    assert res["event_id"] == "evt-1"
    assert res["diary_triggered"] is True  # archive triggers diary
    assert res["duplicate"] is False


def test_first_delivery_with_session_id_returns_canonical_pending():
    # v7.5: diary-triggering action + live session_id → Brain owns the
    # plan and hands it back.
    res = _call(
        event_id="evt-canonical",
        action="archive",
        conversation_id="conv-c",
        session_id="sess-live",
        payload_snapshot=json.dumps({"title": "Alpha"}),
    )
    assert res["status"] == "canonical_pending"
    assert res["canonical_plan_id"].startswith("cpl-")
    assert res["canonical_plan_version"] >= 2
    types = [a["type"] for a in res["canonical_actions"]]
    assert types == ["resume_session", "inject_prompt", "stop_session"]
    kinds = [a["kind"] for a in res["canonical_actions"]]
    assert kinds == types
    # The inject_prompt action must carry the actual prompt text (no
    # Desktop hardcoding).
    inject = next(a for a in res["canonical_actions"] if a["type"] == "inject_prompt")
    assert "nexo_session_diary_write" in inject["payload"]["prompt"]
    # One-release compatibility mirrors for Desktop <= 0.28.1.
    assert inject["kind"] == "inject_prompt"
    assert inject["prompt"] == inject["payload"]["prompt"]
    assert inject["expected_tool_call"] == "nexo_session_diary_write"
    assert inject["timeout_ms"] >= 1_000


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


# ── v7.5 canonical-authority contract ──────────────────────────────


def _complete(event_id, plan_id, results=None):
    from plugins.lifecycle_events import handle_nexo_lifecycle_complete_canonical
    return json.loads(handle_nexo_lifecycle_complete_canonical(
        event_id=event_id,
        canonical_plan_id=plan_id,
        results=json.dumps(results or []),
    ))


def test_canonical_plan_id_is_deterministic_for_same_event():
    # Re-delivery of the SAME event_id must produce the SAME plan id
    # so Desktop can dedupe action-level retries locally.
    res1 = _call(event_id="evt-det", action="archive", conversation_id="c", session_id="s")
    assert res1["status"] == "canonical_pending"
    plan_id_1 = res1["canonical_plan_id"]
    # Second call to the same event_id reads the persisted plan.
    res2 = _call(event_id="evt-det", action="archive", conversation_id="c", session_id="s")
    # Because the row is still in canonical_pending without a
    # canonical_dispatched_at in the past, Brain re-hands it.
    assert res2["canonical_plan_id"] == plan_id_1
    assert res2.get("resumed_from_dispatch") is True


def test_complete_canonical_marks_done_and_persists_results():
    res = _call(event_id="evt-c1", action="close", conversation_id="c", session_id="s1")
    plan_id = res["canonical_plan_id"]
    ack = _complete("evt-c1", plan_id, results=[
        {"action_id": "a1", "status": "ok"},
        {"action_id": "a2", "status": "ok", "tool_called": "nexo_session_diary_write"},
        {"action_id": "a3", "status": "ok"},
    ])
    assert ack["status"] == "canonical_done"
    assert ack["failed_actions"] is False
    # Status endpoint reflects the terminal state.
    from plugins.lifecycle_events import handle_nexo_lifecycle_status
    row = json.loads(handle_nexo_lifecycle_status("evt-c1"))
    assert row["delivery_status"] == "canonical_done"
    assert row["canonical_done_results"]
    assert row["canonical_done_at"]


def test_complete_canonical_with_failure_flips_to_retryable_error():
    res = _call(event_id="evt-fail", action="archive", conversation_id="c", session_id="s")
    plan_id = res["canonical_plan_id"]
    ack = _complete("evt-fail", plan_id, results=[
        {"action_id": "a1", "status": "ok"},
        {"action_id": "a2", "status": "failed", "reason": "stdin-closed"},
    ])
    assert ack["status"] == "retryable_error"
    assert ack["failed_actions"] is True


def test_complete_canonical_rejects_stale_plan_id():
    _call(event_id="evt-stale", action="archive", conversation_id="c", session_id="s")
    ack = _complete("evt-stale", "cpl-wrong-whatever", results=[{"action_id": "a1", "status": "ok"}])
    assert ack["status"] == "rejected"
    assert "canonical_plan_id-mismatch" in ack["reason"]


def test_complete_canonical_rejects_unknown_event():
    ack = _complete("evt-nonexistent", "cpl-xyz", results=[])
    assert ack["status"] == "rejected"
    assert "unknown-event-id" in ack["reason"]


def test_redelivery_after_canonical_done_is_idempotent():
    res = _call(event_id="evt-idem", action="archive", conversation_id="c", session_id="s")
    _complete("evt-idem", res["canonical_plan_id"], results=[{"action_id": "a1", "status": "ok"}])
    # A second record call with the same event_id must short-circuit.
    res2 = _call(event_id="evt-idem", action="archive", conversation_id="c", session_id="s")
    assert res2["status"] == "already_processed"
    assert res2["duplicate"] is True


def test_session_diary_dedup_guards_against_duplicate_plan_execution():
    # Simulates the exact crash-between-dispatch-and-confirm scenario:
    # Brain returned a plan; Desktop executed the inject and the model
    # wrote a session_diary row; Desktop crashed before sending
    # complete_canonical. On the next record_lifecycle_event call for
    # the same event_id, Brain must detect the diary and refuse to
    # re-dispatch.
    res = _call(event_id="evt-crash", action="archive", conversation_id="c", session_id="sess-crash")
    assert res["status"] == "canonical_pending"
    # Simulate the diary write that the live model produced.
    from db import get_db
    get_db().execute(
        "INSERT INTO session_diary (session_id, created_at, summary, decisions) "
        "VALUES (?, datetime('now', '+1 minute'), ?, ?)",
        ("sess-crash", "diary from model", "decisions from model"),
    )
    get_db().commit()
    # Re-deliver. Must short-circuit to already_processed.
    res2 = _call(event_id="evt-crash", action="archive", conversation_id="c", session_id="sess-crash")
    assert res2["status"] == "already_processed"
    assert "session_diary" in (res2.get("reason") or "")


def test_switch_never_produces_canonical_plan_even_with_session_id():
    res = _call(event_id="evt-sw", action="switch", conversation_id="c", session_id="sess-live")
    assert res["status"] == "processed"
    # A switch is observational; there is no canonical_actions field.
    assert "canonical_plan_id" not in res
    assert "canonical_actions" not in res


def test_window_close_never_produces_canonical_plan_even_with_session_id():
    res = _call(event_id="evt-wc", action="window-close", conversation_id="c", session_id="sess-live")
    assert res["status"] == "processed"
    assert "canonical_plan_id" not in res
    assert "canonical_actions" not in res


def test_app_exit_with_live_session_uses_real_desktop_action_shape():
    res = _call(event_id="evt-app-exit", action="app-exit", conversation_id="c", session_id="sess-live")
    assert res["status"] == "canonical_pending"
    assert res["canonical_plan_version"] >= 2
    actions = res["canonical_actions"]
    assert [a["type"] for a in actions] == ["resume_session", "inject_prompt", "stop_session"]
    assert [a["kind"] for a in actions] == ["resume_session", "inject_prompt", "stop_session"]
    inject = actions[1]
    assert inject["payload"]["prompt"] == inject["prompt"]
    assert "NEXO Desktop" in inject["payload"]["prompt"]


def test_canonical_plan_id_deterministic_function():
    # Pure-function contract: same input → same plan id.
    import lifecycle_prompts as lp
    a = lp.canonical_plan_id("evt-abc", 1)
    b = lp.canonical_plan_id("evt-abc", 1)
    c = lp.canonical_plan_id("evt-abc", 2)
    assert a == b
    assert a != c  # different version bumps the id


def test_canonical_actions_json_round_trip_via_status():
    res = _call(event_id="evt-rt", action="delete", conversation_id="c", session_id="sess-rt")
    assert res["status"] == "canonical_pending"
    from plugins.lifecycle_events import handle_nexo_lifecycle_status
    row = json.loads(handle_nexo_lifecycle_status("evt-rt"))
    assert row["canonical_plan_id"] == res["canonical_plan_id"]
    assert row["canonical_plan_version"] == res["canonical_plan_version"]
    # Actions stored verbatim so a crashed Desktop can re-execute the
    # exact same plan on next boot.
    assert row["canonical_actions"][0]["type"] == "resume_session"
    assert row["canonical_actions"][1]["type"] == "inject_prompt"
    assert row["canonical_actions"][2]["type"] == "stop_session"
    assert row["canonical_actions"][0]["kind"] == "resume_session"
    assert row["canonical_actions"][1]["kind"] == "inject_prompt"
    assert row["canonical_actions"][2]["kind"] == "stop_session"
    assert row["canonical_actions"][1]["payload"]["prompt"] == row["canonical_actions"][1]["prompt"]
    assert row["canonical_dispatched_at"]
