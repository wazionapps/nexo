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
    assert res["canonical_plan_version"] >= 4
    types = [a["type"] for a in res["canonical_actions"]]
    assert types == ["resume_session", "inject_prompt", "wait_for_diary_write", "stop_session", "wait_for_stop"]
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
    assert inject["timeout_ms"] >= 30_000
    wait = next(a for a in res["canonical_actions"] if a["type"] == "wait_for_diary_write")
    assert wait["event_id"] == "evt-canonical"
    assert wait["expected_tool_call"] == "nexo_session_diary_write"
    assert wait["evidence"] == "session_diary"
    assert wait["timeout_ms"] >= 30_000
    wait_stop = next(a for a in res["canonical_actions"] if a["type"] == "wait_for_stop")
    assert wait_stop["event_id"] == "evt-canonical"
    assert wait_stop["expected_tool_call"] == "nexo_stop"
    assert wait_stop["evidence"] == "session_stop"


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
    from db import get_db, register_session, complete_session

    register_session("nexo-9100-1", "close lifecycle")
    res = _call(event_id="evt-c1", action="close", conversation_id="c", session_id="nexo-9100-1")
    plan_id = res["canonical_plan_id"]
    get_db().execute(
        "INSERT INTO session_diary (session_id, created_at, summary, decisions) "
        "VALUES (?, datetime('now', '+1 minute'), ?, ?)",
        ("nexo-9100-1", "canonical diary", "canonical decisions"),
    )
    get_db().commit()
    complete_session("nexo-9100-1")
    ack = _complete("evt-c1", plan_id, results=[
        {"action_id": "a1", "status": "ok"},
        {"action_id": "a2", "status": "ok", "tool_called": "nexo_session_diary_write"},
        {"action_id": "a3", "type": "wait_for_diary_write", "status": "ok", "diary_confirmed": True},
        {"action_id": "a4", "status": "ok"},
        {"action_id": "a5", "type": "wait_for_stop", "status": "ok", "stop_confirmed": True},
    ])
    assert ack["status"] == "canonical_done"
    assert ack["failed_actions"] is False
    assert ack["diary_confirmed"] is True
    assert ack["stop_confirmed"] is True
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


def test_complete_canonical_rejects_done_without_real_diary_evidence():
    res = _call(event_id="evt-no-diary", action="archive", conversation_id="c", session_id="nexo-9000-2")
    plan_id = res["canonical_plan_id"]
    ack = _complete("evt-no-diary", plan_id, results=[
        {"action_id": "a1", "status": "ok"},
        {"action_id": "a2", "status": "ok"},
        {"action_id": "a3", "type": "wait_for_diary_write", "status": "ok", "diary_confirmed": True},
        {"action_id": "a4", "status": "ok"},
        {"action_id": "a5", "type": "wait_for_stop", "status": "ok", "stop_confirmed": True},
    ])
    assert ack["status"] == "retryable_error"
    assert ack["diary_confirmed"] is False
    assert "diary" in (ack.get("reason") or "")
    from plugins.lifecycle_events import handle_nexo_lifecycle_status
    row = json.loads(handle_nexo_lifecycle_status("evt-no-diary"))
    assert row["delivery_status"] == "retryable_error"
    assert row["canonical_done_at"] is None


def test_complete_canonical_rejects_stale_plan_id():
    _call(event_id="evt-stale", action="archive", conversation_id="c", session_id="s")
    ack = _complete("evt-stale", "cpl-wrong-whatever", results=[{"action_id": "a1", "status": "ok"}])
    assert ack["status"] == "rejected"
    assert "canonical_plan_id-mismatch" in ack["reason"]


def test_complete_canonical_rejects_unknown_event():
    ack = _complete("evt-nonexistent", "cpl-xyz", results=[])
    assert ack["status"] == "rejected"
    assert "unknown-event-id" in ack["reason"]


def test_wait_for_diary_uses_session_diary_evidence():
    _call(event_id="evt-wait", action="archive", conversation_id="c", session_id="nexo-9000-1")
    from plugins.lifecycle_events import handle_nexo_lifecycle_wait_for_diary
    miss = json.loads(handle_nexo_lifecycle_wait_for_diary("evt-wait", timeout_ms=1, poll_ms=1))
    assert miss["status"] == "retryable_error"
    assert miss["diary_confirmed"] is False

    from db import get_db
    get_db().execute(
        "INSERT INTO session_diary (session_id, summary, decisions) VALUES (?, ?, ?)",
        ("nexo-9000-1", "diary after dispatch", "decisions"),
    )
    get_db().commit()

    hit = json.loads(handle_nexo_lifecycle_wait_for_diary("evt-wait", timeout_ms=100, poll_ms=1))
    assert hit["status"] == "ok"
    assert hit["diary_confirmed"] is True
    assert hit["session_diary_id"]


def test_write_fallback_diary_preserves_payload_snapshot_when_injection_fails():
    from db import complete_session, get_db, register_session
    from plugins.lifecycle_events import (
        handle_nexo_lifecycle_wait_for_diary,
        handle_nexo_lifecycle_write_fallback_diary,
    )

    register_session("nexo-9102-1", "desktop close fallback")
    payload = {
        "title": "Cerrar app con trabajo vivo",
        "current_goal": "No perder contexto al cerrar Desktop",
        "transcript_tail": [
            "user: arregla el cierre de diarios",
            "assistant: detectado inject-response-timeout",
        ],
    }
    res = _call(
        event_id="evt-fallback-diary",
        action="app-exit",
        conversation_id="conv-fallback",
        session_id="nexo-9102-1",
        payload_snapshot=json.dumps(payload, ensure_ascii=False),
    )
    fallback = json.loads(handle_nexo_lifecycle_write_fallback_diary(
        "evt-fallback-diary",
        reason="inject-response-timeout",
    ))
    assert fallback["status"] == "ok"
    assert fallback["fallback_written"] is True
    assert fallback["diary_session_id"] == "nexo-9102-1"

    hit = json.loads(handle_nexo_lifecycle_wait_for_diary("evt-fallback-diary", timeout_ms=100, poll_ms=1))
    assert hit["status"] == "ok"
    assert hit["session_diary_id"] == fallback["session_diary_id"]

    row = get_db().execute(
        "SELECT summary, pending, context_next, source FROM session_diary WHERE id = ?",
        (fallback["session_diary_id"],),
    ).fetchone()
    assert "Cerrar app con trabajo vivo" in row["summary"]
    assert "No perder contexto" in row["pending"]
    assert "inject-response-timeout" in row["context_next"] or "arregla el cierre" in row["context_next"]
    assert row["source"] == "desktop-lifecycle-fallback"

    complete_session("nexo-9102-1")
    ack = _complete("evt-fallback-diary", res["canonical_plan_id"], results=[
        {"action_id": "a1", "type": "resume_session", "status": "ok"},
        {"action_id": "a2", "type": "inject_prompt", "status": "ok", "fallback_diary": True},
        {"action_id": "a3", "type": "wait_for_diary_write", "status": "ok", "diary_confirmed": True},
        {"action_id": "a4", "type": "stop_session", "status": "ok"},
        {"action_id": "a5", "type": "wait_for_stop", "status": "ok", "stop_confirmed": True},
    ])
    assert ack["status"] == "canonical_done"
    assert ack["diary_confirmed"] is True


def test_write_fallback_diary_enriches_minimal_lifecycle_payload_from_continuity():
    from db import get_db, register_session
    from plugins.lifecycle_events import handle_nexo_lifecycle_write_fallback_diary

    register_session("nexo-9103-1", "desktop close fallback continuity")
    conn = get_db()
    snapshots = [
        {
            "current_goal": "mensaje 1",
            "last_user_message": "mensaje 1",
            "last_assistant_message": "OK 1",
            "transcript_tail": ["user: mensaje 1", "assistant: OK 1"],
        },
        {
            "current_goal": "mensaje 2",
            "last_user_message": "mensaje 2",
            "last_assistant_message": "OK 2",
            "transcript_tail": ["user: mensaje 2", "assistant: OK 2"],
        },
        {
            "current_goal": "mensaje 3",
            "last_user_message": "mensaje 3",
            "last_assistant_message": "OK 3",
            "transcript_tail": ["user: mensaje 3", "assistant: OK 3"],
        },
    ]
    for idx, payload in enumerate(snapshots, start=1):
        conn.execute(
            """
            INSERT INTO continuity_snapshots (
                conversation_id, session_id, event_type, payload_json,
                trace_id, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "conv-continuity-fallback",
                "nexo-9103-1",
                "turn_end",
                json.dumps(payload, ensure_ascii=False),
                f"trace-{idx}",
                f"idem-{idx}",
            ),
        )
    conn.commit()

    _call(
        event_id="evt-fallback-continuity",
        action="app-exit",
        conversation_id="conv-continuity-fallback",
        session_id="nexo-9103-1",
        payload_snapshot=json.dumps({"title": "New conversation", "is_active": True}, ensure_ascii=False),
    )
    fallback = json.loads(handle_nexo_lifecycle_write_fallback_diary(
        "evt-fallback-continuity",
        reason="inject-response-timeout",
    ))
    assert fallback["status"] == "ok"

    row = conn.execute(
        "SELECT pending, context_next, source FROM session_diary WHERE id = ?",
        (fallback["session_diary_id"],),
    ).fetchone()
    assert row["pending"] == "mensaje 3"
    assert "user: mensaje 1" in row["context_next"]
    assert "assistant: OK 3" in row["context_next"]
    assert row["source"] == "desktop-lifecycle-fallback"


def test_wait_for_stop_uses_active_session_absence_as_evidence():
    from db import register_session, complete_session
    from plugins.lifecycle_events import handle_nexo_lifecycle_wait_for_stop

    register_session("nexo-9101-1", "archive lifecycle")
    _call(event_id="evt-wait-stop", action="archive", conversation_id="c", session_id="nexo-9101-1")

    miss = json.loads(handle_nexo_lifecycle_wait_for_stop("evt-wait-stop", timeout_ms=1, poll_ms=1))
    assert miss["status"] == "retryable_error"
    assert miss["stop_confirmed"] is False
    assert miss["session_registered"] is True
    assert "nexo-9101-1" in miss["active_session_ids"]

    complete_session("nexo-9101-1")

    hit = json.loads(handle_nexo_lifecycle_wait_for_stop("evt-wait-stop", timeout_ms=100, poll_ms=1))
    assert hit["status"] == "ok"
    assert hit["stop_confirmed"] is True


def test_wait_for_diary_fails_fast_when_session_is_not_linked_to_nexo():
    _call(
        event_id="evt-unregistered-wait",
        action="archive",
        conversation_id="c",
        session_id="claude-session-without-nexo-link",
    )
    from plugins.lifecycle_events import handle_nexo_lifecycle_wait_for_diary
    miss = json.loads(handle_nexo_lifecycle_wait_for_diary("evt-unregistered-wait", timeout_ms=100, poll_ms=1))
    assert miss["status"] == "retryable_error"
    assert miss["diary_confirmed"] is False
    assert miss["session_registered"] is False
    assert miss["reason"] == "session-not-linked-to-nexo"


def test_wait_for_diary_accepts_nexo_sid_alias_for_desktop_session_uuid():
    from db import get_db

    conn = get_db()
    conn.execute(
        "INSERT INTO session_claude_aliases (sid, claude_session_id, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?)",
        ("nexo-alias-sid", "desktop-session-uuid", 1, 1),
    )
    conn.execute(
        "INSERT INTO session_diary (session_id, created_at, summary, decisions) "
        "VALUES (?, datetime('now', '-1 minute'), ?, ?)",
        ("nexo-alias-sid", "old diary must not satisfy checkpoint", "old"),
    )
    conn.commit()

    res = _call(
        event_id="evt-alias-diary",
        action="archive",
        conversation_id="c",
        session_id="desktop-session-uuid",
    )
    wait = next(a for a in res["canonical_actions"] if a["type"] == "wait_for_diary_write")
    assert wait["after_session_diary_id"] > 0

    from plugins.lifecycle_events import handle_nexo_lifecycle_wait_for_diary
    miss = json.loads(handle_nexo_lifecycle_wait_for_diary("evt-alias-diary", timeout_ms=1, poll_ms=1))
    assert miss["status"] == "retryable_error"

    conn.execute(
        "INSERT INTO session_diary (session_id, summary, decisions) VALUES (?, ?, ?)",
        ("nexo-alias-sid", "canonical diary through NEXO SID alias", "decisions"),
    )
    conn.commit()

    hit = json.loads(handle_nexo_lifecycle_wait_for_diary("evt-alias-diary", timeout_ms=100, poll_ms=1))
    assert hit["status"] == "ok"
    assert hit["diary_confirmed"] is True
    assert hit["diary_session_id"] == "nexo-alias-sid"


def test_fallback_diary_prefers_registered_session_over_latest_orphan_alias():
    from db import complete_session, get_db, register_session
    from plugins.lifecycle_events import (
        handle_nexo_lifecycle_wait_for_stop,
        handle_nexo_lifecycle_write_fallback_diary,
    )

    conn = get_db()
    register_session(
        "nexo-9200-1",
        "desktop lifecycle close",
        external_session_id="desktop-session-uuid",
        session_client="desktop",
        conversation_id="c",
    )
    conn.execute(
        "INSERT INTO session_claude_aliases (sid, claude_session_id, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?)",
        ("nexo-9200-2", "desktop-session-uuid", 1, 200),
    )
    conn.execute(
        "INSERT INTO session_claude_aliases (sid, claude_session_id, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?)",
        ("nexo-9200-1", "desktop-session-uuid", 1, 100),
    )
    conn.commit()

    res = _call(
        event_id="evt-fallback-real-session",
        action="app-exit",
        conversation_id="c",
        session_id="desktop-session-uuid",
        payload_snapshot=json.dumps({
            "title": "real desktop close",
            "messages": [
                {"role": "user", "content": "mensaje 1"},
                {"role": "assistant", "content": "respuesta 1"},
            ],
        }),
    )
    assert res["status"] == "canonical_pending"

    fallback = json.loads(handle_nexo_lifecycle_write_fallback_diary(
        "evt-fallback-real-session",
        reason="inject-response-timeout",
    ))
    assert fallback["status"] == "ok"
    assert fallback["diary_session_id"] == "nexo-9200-1"

    miss = json.loads(handle_nexo_lifecycle_wait_for_stop(
        "evt-fallback-real-session",
        timeout_ms=1,
        poll_ms=1,
    ))
    assert miss["status"] == "retryable_error"
    assert "nexo-9200-1" in miss["active_session_ids"]

    complete_session("nexo-9200-1")

    hit = json.loads(handle_nexo_lifecycle_wait_for_stop(
        "evt-fallback-real-session",
        timeout_ms=100,
        poll_ms=1,
    ))
    assert hit["status"] == "ok"
    assert hit["stop_confirmed"] is True


def test_stop_nexo_session_resolves_orphan_alias_to_registered_session():
    from db import get_db, register_session
    from plugins.lifecycle_events import handle_nexo_lifecycle_stop_nexo_session

    conn = get_db()
    register_session(
        "nexo-9300-1",
        "desktop lifecycle close",
        external_session_id="desktop-session-stop",
        session_client="desktop",
        conversation_id="c",
    )
    conn.execute(
        "INSERT INTO session_claude_aliases (sid, claude_session_id, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?)",
        ("nexo-9300-2", "desktop-session-stop", 1, 200),
    )
    conn.execute(
        "INSERT INTO session_claude_aliases (sid, claude_session_id, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?)",
        ("nexo-9300-1", "desktop-session-stop", 1, 100),
    )
    conn.commit()

    ack = json.loads(handle_nexo_lifecycle_stop_nexo_session("nexo-9300-2"))
    assert ack["status"] == "ok"
    assert ack["stopped_session_ids"] == ["nexo-9300-1"]

    row = conn.execute("SELECT 1 FROM sessions WHERE sid = ?", ("nexo-9300-1",)).fetchone()
    assert row is None


def test_complete_canonical_returns_session_not_linked_reason_for_unregistered_session():
    res = _call(
        event_id="evt-unregistered-complete",
        action="archive",
        conversation_id="c",
        session_id="claude-session-without-link-2",
    )
    ack = _complete("evt-unregistered-complete", res["canonical_plan_id"], results=[
        {"action_id": "a1", "status": "ok"},
        {"action_id": "a2", "status": "ok"},
        {
            "action_id": "a3",
            "type": "wait_for_diary_write",
            "status": "failed",
            "diary_confirmed": False,
            "reason": "session-not-linked-to-nexo",
        },
        {"action_id": "a4", "status": "blocked", "reason": "blocked-by-prior-failure"},
        {"action_id": "a5", "status": "blocked", "reason": "blocked-by-prior-failure"},
    ])
    assert ack["status"] == "retryable_error"
    assert ack["session_registered"] is False
    assert ack["reason"] == "session-not-linked-to-nexo"


def test_redelivery_after_canonical_done_is_idempotent():
    res = _call(event_id="evt-idem", action="archive", conversation_id="c", session_id="s")
    from db import get_db
    get_db().execute(
        "INSERT INTO session_diary (session_id, created_at, summary, decisions) "
        "VALUES (?, datetime('now', '+1 minute'), ?, ?)",
        ("s", "diary from model", "decisions from model"),
    )
    get_db().commit()
    _complete("evt-idem", res["canonical_plan_id"], results=[
        {"action_id": "a1", "status": "ok"},
        {"action_id": "a2", "status": "ok"},
        {"action_id": "a3", "type": "wait_for_diary_write", "status": "ok", "diary_confirmed": True},
        {"action_id": "a4", "status": "ok"},
    ])
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
    # Re-deliver. Diary alone is no longer enough; Brain must re-hand the
    # same plan until the linked stop is confirmed too.
    res2 = _call(event_id="evt-crash", action="archive", conversation_id="c", session_id="sess-crash")
    assert res2["status"] == "canonical_pending"
    assert res2.get("resumed_from_dispatch") is True


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
    assert res["canonical_plan_version"] >= 4
    actions = res["canonical_actions"]
    assert [a["type"] for a in actions] == ["resume_session", "inject_prompt", "wait_for_diary_write", "stop_session", "wait_for_stop"]
    assert [a["kind"] for a in actions] == ["resume_session", "inject_prompt", "wait_for_diary_write", "stop_session", "wait_for_stop"]
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
    assert row["canonical_actions"][2]["type"] == "wait_for_diary_write"
    assert row["canonical_actions"][3]["type"] == "stop_session"
    assert row["canonical_actions"][4]["type"] == "wait_for_stop"
    assert row["canonical_actions"][0]["kind"] == "resume_session"
    assert row["canonical_actions"][1]["kind"] == "inject_prompt"
    assert row["canonical_actions"][2]["kind"] == "wait_for_diary_write"
    assert row["canonical_actions"][3]["kind"] == "stop_session"
    assert row["canonical_actions"][4]["kind"] == "wait_for_stop"
    assert row["canonical_actions"][1]["payload"]["prompt"] == row["canonical_actions"][1]["prompt"]
    assert row["canonical_actions"][2]["payload"]["after_session_diary_id"] >= 0
    assert row["canonical_dispatched_at"]
