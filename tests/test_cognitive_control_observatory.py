from __future__ import annotations


def test_cognitive_control_observatory_is_read_only(isolated_db):
    import db
    from cognitive_control_observatory import build_cognitive_control_observatory
    from local_context import usage_events

    db.create_followup("NF-OBS", "Observe followup lifecycle", date="2026-01-01", status="PENDING")
    db.create_learning("nexo-ops", "Observe learnings", "Learning observatory test.")
    event = db.record_memory_event(
        event_type="protocol_task_done",
        source_type="protocol_task",
        source_id="PT-OBS",
        metadata={"goal": "Observatory read only", "outcome": "done"},
        idempotency_key="obs",
        created_at=1000.0,
    )
    usage_events.record_usage_event(
        query="where is observatory",
        tool="local_context",
        source="local_context",
        route_stage="pre_answer:shadow",
        intent="file_location",
        created_at=1000.0,
    )

    conn = db.get_db()
    before_followups = conn.execute("SELECT COUNT(*) FROM followups").fetchone()[0]
    before_queue = conn.execute("SELECT COUNT(*) FROM memory_observation_queue").fetchone()[0]

    payload = build_cognitive_control_observatory(window_seconds=86400, now_ts=1100.0)

    after_followups = conn.execute("SELECT COUNT(*) FROM followups").fetchone()[0]
    after_queue = conn.execute("SELECT COUNT(*) FROM memory_observation_queue").fetchone()[0]

    assert event["ok"] is True
    assert payload["read_only"] is True
    assert payload["phase_coverage"]["phase_0_observatory"] is True
    assert payload["local_context"]["usage"]["by_source"]["local_context"] == 1
    assert payload["learnings"]["active"] == 1
    assert payload["followups"]["counts"]["active"] == 1
    assert payload["intraday_memory"]["health"]["counts"]["events"] == 1
    assert after_followups == before_followups
    assert after_queue == before_queue
