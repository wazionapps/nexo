from __future__ import annotations


def _queue_status_counts(db_module) -> dict[str, int]:
    conn = db_module.get_db()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM memory_observation_queue GROUP BY status"
    ).fetchall()
    return {row["status"]: int(row["cnt"]) for row in rows}


def test_new_event_becomes_queryable_and_processing_is_idempotent(isolated_db):
    import db
    import memory_observation_processor as processor

    event = db.record_memory_event(
        event_type="tool_write",
        source_type="tool",
        source_id="tool-pipeline-1",
        session_id="nexo-pipeline",
        tool_name="Edit",
        file_paths=["src/pipeline.py"],
        metadata={"summary": "Edit wrote src/pipeline.py for the observation pipeline"},
        idempotency_key="tool-pipeline-1",
        created_at=1000.0,
    )

    first = processor.process_incremental(process_limit=10, backfill_limit=10, now=1010.0)

    assert event["ok"] is True
    assert first["ok"] is True
    assert first["processed"]["processed"] == 1
    assert _queue_status_counts(db)["processed"] == 1

    observations = db.list_memory_observations(query="pipeline.py", limit=10)
    assert len(observations) == 1
    assert observations[0]["evidence_refs"][0] == f"memory_event:{event['event_uid']}"

    second = processor.process_incremental(process_limit=10, backfill_limit=10, now=1020.0)

    assert second["processed"]["processed"] == 0
    assert _queue_status_counts(db)["processed"] == 1
    assert len(db.list_memory_observations(query="pipeline.py", limit=10)) == 1


def test_high_salience_observation_publishes_intraday_fact(isolated_db):
    import db
    import memory_observation_processor as processor

    event = db.record_memory_event(
        event_type="protocol_task_done",
        source_type="protocol_task",
        source_id="PT-INTRADAY",
        session_id="nexo-intraday",
        project_key="nexo",
        metadata={"goal": "Implement cognitive control branch", "outcome": "done"},
        idempotency_key="pt-intraday",
        created_at=1000.0,
    )

    result = processor.process_incremental(process_limit=10, backfill_limit=10, now=1010.0)
    observation_uid = processor.observation_uid_for_event(event["event_uid"])
    hot = db.get_hot_context(f"intraday_fact:{observation_uid}", include_events=True)

    assert result["processed"]["processed"] == 1
    assert result["processed"]["intraday_facts"] == 1
    assert hot is not None
    assert hot["context_type"] == "intraday_fact"
    assert hot["source_type"] == "memory_observation"
    assert hot["source_id"] == observation_uid


def test_unverified_code_change_does_not_publish_intraday_fact(isolated_db):
    import db
    import memory_observation_processor as processor

    event = db.record_memory_event(
        event_type="tool_write",
        source_type="tool",
        source_id="tool-unverified",
        session_id="nexo-intraday",
        tool_name="Edit",
        file_paths=["src/unverified.py"],
        metadata={"summary": "Edit wrote src/unverified.py without verification"},
        idempotency_key="tool-unverified",
        created_at=1000.0,
    )

    result = processor.process_intraday_cycle(process_limit=10, backfill_limit=10, now=1010.0)
    observation_uid = processor.observation_uid_for_event(event["event_uid"])
    hot = db.get_hot_context(f"intraday_fact:{observation_uid}", include_events=True)

    assert result["mode"] == "intraday"
    assert result["processed"]["processed"] == 1
    assert result["processed"]["intraday_facts"] == 0
    assert hot is None


def test_verified_code_change_can_publish_intraday_fact(isolated_db):
    import db
    import memory_observation_processor as processor

    event = db.record_memory_event(
        event_type="tool_write",
        source_type="tool",
        source_id="tool-verified",
        session_id="nexo-intraday",
        tool_name="Edit",
        file_paths=["src/verified.py"],
        metadata={"summary": "Edit wrote src/verified.py", "test_output": "pytest passed"},
        idempotency_key="tool-verified",
        created_at=1000.0,
    )

    result = processor.process_intraday_cycle(process_limit=10, backfill_limit=10, now=1010.0)
    observation_uid = processor.observation_uid_for_event(event["event_uid"])
    hot = db.get_hot_context(f"intraday_fact:{observation_uid}", include_events=True)

    assert result["processed"]["intraday_facts"] == 1
    assert hot is not None
    assert hot["context_type"] == "intraday_fact"


def test_backfill_incrementally_queues_memory_events_without_duplicates(isolated_db):
    import db
    import memory_observation_processor as processor

    for index in range(2):
        db.record_memory_event(
            event_type="tool_write",
            source_type="tool",
            source_id=f"tool-backfill-{index}",
            session_id="nexo-backfill",
            tool_name="Write",
            file_paths=[f"src/backfill_{index}.py"],
            metadata={"summary": f"Write created src/backfill_{index}.py"},
            idempotency_key=f"tool-backfill-{index}",
            created_at=1000.0 + index,
            enqueue_observation=False,
        )

    assert _queue_status_counts(db) == {}

    first = processor.process_incremental(process_limit=10, backfill_limit=1, now=1100.0)
    second = processor.process_incremental(process_limit=10, backfill_limit=1, now=1110.0)
    third = processor.process_incremental(process_limit=10, backfill_limit=1, now=1120.0)

    assert first["backfill"]["enqueued"] == 1
    assert first["backfill"]["remaining"] == 1
    assert second["backfill"]["enqueued"] == 1
    assert second["backfill"]["remaining"] == 0
    assert third["backfill"]["enqueued"] == 0
    assert third["processed"]["processed"] == 0
    assert _queue_status_counts(db)["processed"] == 2
    assert len(db.list_memory_observations(query="backfill_", limit=10)) == 2


def test_pending_sla_reports_oldest_pending_event(isolated_db):
    import db
    import memory_observation_processor as processor

    event = db.record_memory_event(
        event_type="tool_write",
        source_type="tool",
        source_id="tool-stale",
        session_id="nexo-stale",
        tool_name="Edit",
        file_paths=["src/stale.py"],
        idempotency_key="tool-stale",
        created_at=1000.0,
    )

    health = processor.queue_health(pending_sla_seconds=3600, now=1000.0 + 7200.0)

    assert health["ok"] is True
    assert health["healthy"] is False
    assert health["pending_sla_ok"] is False
    assert health["pending_older_than_sla"] == 1
    assert health["oldest_pending"]["event_uid"] == event["event_uid"]
    assert any(warning["code"] == "pending_sla_breached" for warning in health["warnings"])


def test_processed_queue_without_observation_is_repaired(isolated_db):
    import db
    import memory_observation_processor as processor

    event = db.record_memory_event(
        event_type="tool_write",
        source_type="tool",
        source_id="tool-repair",
        session_id="nexo-repair",
        tool_name="Edit",
        file_paths=["src/repair.py"],
        metadata={"summary": "Edit wrote src/repair.py"},
        idempotency_key="tool-repair",
        created_at=1000.0,
    )
    processor.process_incremental(process_limit=10, backfill_limit=10, now=1010.0)

    conn = db.get_db()
    conn.execute(
        "DELETE FROM memory_observations WHERE observation_uid = ?",
        (processor.observation_uid_for_event(event["event_uid"]),),
    )
    conn.commit()

    broken_health = processor.queue_health(pending_sla_seconds=3600, now=1020.0)
    repaired = processor.process_incremental(process_limit=10, backfill_limit=10, now=1030.0)

    assert broken_health["processed_missing_observations"] == 1
    assert any(warning["code"] == "processed_missing_observation" for warning in broken_health["warnings"])
    assert repaired["repair"]["requeued"] == 1
    assert repaired["processed"]["processed"] == 1
    assert len(db.list_memory_observations(query="repair.py", limit=10)) == 1
