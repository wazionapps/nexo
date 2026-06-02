from __future__ import annotations

import json
import sqlite3
import time


def _indexes(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA index_list({table})").fetchall()}


def _cols(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _insert_stm(*, strength: float = 0.5, stability: float = 0.5, difficulty: float = 0.5, lifecycle_state: str = "active", source_type: str = "task") -> int:
    import cognitive

    conn = cognitive._get_db()
    cur = conn.execute(
        """
        INSERT INTO stm_memories (
            content, embedding, source_type, source_id, domain,
            strength, stability, difficulty, lifecycle_state
        )
        VALUES (?, ?, ?, 'source', 'nexo', ?, ?, ?, ?)
        """,
        ("useful memory", b"", source_type, strength, stability, difficulty, lifecycle_state),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_ltm(*, strength: float = 0.5, stability: float = 0.5, difficulty: float = 0.5, tags: str = "") -> int:
    import cognitive

    conn = cognitive._get_db()
    cur = conn.execute(
        """
        INSERT INTO ltm_memories (
            content, embedding, source_type, source_id, domain,
            strength, stability, difficulty, tags
        )
        VALUES (?, ?, 'task', 'source', 'nexo', ?, ?, ?, ?)
        """,
        ("long memory", b"", strength, stability, difficulty, tags),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_observation(uid: str = "obs-1") -> str:
    import db

    now = time.time()
    db.get_db().execute(
        """
        INSERT INTO memory_observations (
            observation_uid, created_at, updated_at, project_key,
            session_id, observation_type, subject, summary,
            evidence_refs_json, salience, confidence, stability
        )
        VALUES (?, ?, ?, 'nexo', 'sid', 'fact', 'subject', 'summary', '[]', 0.5, 0.5, 0.5)
        """,
        (uid, now, now),
    )
    db.get_db().commit()
    return uid


def _insert_memory_event(uid: str = "event-1") -> str:
    import db

    db.get_db().execute(
        """
        INSERT INTO memory_events (
            event_uid, created_at, source_type, event_type, privacy_level
        )
        VALUES (?, ?, 'test', 'tool', 'normal')
        """,
        (uid, time.time()),
    )
    db.get_db().commit()
    return uid


def _insert_commitment(commitment_id: str = "commit-1") -> str:
    import db

    now = time.time()
    db.get_db().execute(
        """
        INSERT INTO commitments (
            id, created_at, updated_at, source_type, source_id,
            session_id, project_key, statement, owner, status
        )
        VALUES (?, ?, ?, 'test', 'spec03', 'sid', 'nexo', 'Hare la validacion de release.', 'agent', 'active')
        """,
        (commitment_id, now, now),
    )
    db.get_db().commit()
    return commitment_id


def _insert_change() -> int:
    import db

    row = db.log_change("sid", "src/memory_utility.py", "Add memory utility", "Spec 03")
    return int(row["id"])


def _insert_protocol_task(task_id: str = "PT-1") -> str:
    import db

    db.get_db().execute(
        """
        INSERT INTO protocol_tasks (
            task_id, session_id, goal, task_type, status, opened_at
        )
        VALUES (?, 'sid', 'goal', 'edit', 'active', datetime('now'))
        """,
        (task_id,),
    )
    db.get_db().commit()
    return task_id


def _insert_workflow_run(run_id: str = "WF-1") -> str:
    import db

    db.get_db().execute(
        """
        INSERT INTO workflow_runs (
            run_id, session_id, goal, workflow_kind, status,
            idempotency_key, opened_at, updated_at
        )
        VALUES (?, 'sid', 'goal', 'spec', 'running', 'idem', datetime('now'), datetime('now'))
        """,
        (run_id,),
    )
    db.get_db().commit()
    return run_id


def _insert_workflow_checkpoint(checkpoint_id: int = 7) -> int:
    import db

    _insert_workflow_run("WF-CP")
    db.get_db().execute(
        """
        INSERT INTO workflow_checkpoints (
            id, run_id, step_key, checkpoint_label, run_status,
            step_status, summary, created_at
        )
        VALUES (?, 'WF-CP', 's', 'c', 'running', 'done', 'summary', datetime('now'))
        """,
        (checkpoint_id,),
    )
    db.get_db().commit()
    return checkpoint_id


def _insert_session_diary() -> int:
    import db

    db.get_db().execute(
        """
        INSERT INTO session_diary (
            session_id, decisions, discarded, pending, context_next, summary
        )
        VALUES ('sid', '[]', '', '', '', 'diary')
        """
    )
    db.get_db().commit()
    return int(db.get_db().execute("SELECT MAX(id) FROM session_diary").fetchone()[0])


def _insert_learning() -> int:
    import db

    row = db.create_learning(
        category="test",
        title="Memory utility learning",
        content="Use evidence before score changes.",
    )
    return int(row["id"])


def _insert_causal_edge() -> str:
    import causal_graph

    result = causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/memory_utility.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_memory_utility.py",
        reason_public="Spec 03 implementation verified by tests.",
        evidence_refs=["pytest:tests/test_memory_utility.py"],
        project_key="nexo",
        confidence=0.95,
    )
    assert result["ok"] is True
    return result["edge_uid"]


def _record_helpful(memory_ref: str, suffix: str, *, now: float = 1000.0):
    import memory_utility

    return memory_utility.record_use_event(
        memory_ref=memory_ref,
        retrieval_trace_id=f"trace-{suffix}",
        consumer_ref=f"answer-{suffix}",
        use_stage="cited",
        outcome="helpful",
        cited_in_answer=True,
        validated_by_ref=f"test:{suffix}",
        evidence_refs=[f"pytest:{suffix}"],
        query_text=f"How to use memory {suffix}",
        reason_code="task_evidence",
        now=now,
    )


def _record_harmful(memory_ref: str, suffix: str, *, now: float = 1000.0):
    import memory_utility

    return memory_utility.record_use_event(
        memory_ref=memory_ref,
        retrieval_trace_id=f"trace-bad-{suffix}",
        consumer_ref=f"answer-bad-{suffix}",
        use_stage="cited",
        outcome="harmful",
        cited_in_answer=True,
        validated_by_ref=f"correction:{suffix}",
        evidence_refs=[f"correction:{suffix}"],
        reason_code="explicit_correction",
        now=now,
    )


def test_memory_use_events_schema_indexes_and_release_paths(isolated_db):
    import db
    from db import _schema

    conn = db.get_db()
    assert {
        "event_uid",
        "retrieval_trace_id",
        "route_event_id",
        "memory_ref",
        "memory_kind",
        "query_hash",
        "query_preview_redacted",
        "use_stage",
        "outcome",
        "validated_by_ref",
        "evidence_refs_json",
        "policy_version",
        "metadata_json",
    } <= _cols(conn, "memory_use_events")
    assert {
        "idx_memory_use_events_memory_created",
        "idx_memory_use_events_memory_reason",
        "idx_memory_use_events_trace_stage",
        "idx_memory_use_events_query_created",
        "idx_memory_use_events_stage_outcome",
        "idx_memory_use_events_policy_created",
    } <= _indexes(conn, "memory_use_events")
    assert {
        "idx_memory_utility_app_memory",
        "idx_memory_utility_app_policy",
        "idx_memory_utility_app_rollback",
    } <= _indexes(conn, "memory_utility_applications")
    assert {"idx_memory_utility_app_events_event"} <= _indexes(conn, "memory_utility_application_events")

    update_conn = sqlite3.connect(":memory:")
    update_conn.row_factory = sqlite3.Row
    _schema._m72_memory_utility(update_conn)
    _schema._m72_memory_utility(update_conn)
    assert "memory_use_events" in {
        row["name"] for row in update_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"idx_memory_use_events_memory_created"} <= _indexes(update_conn, "memory_use_events")


def test_memory_ref_canonical_validators(isolated_db):
    import memory_utility

    stm_id = _insert_stm()
    ltm_id = _insert_ltm()
    obs_uid = _insert_observation()
    event_uid = _insert_memory_event()
    commitment_id = _insert_commitment()
    edge_uid = _insert_causal_edge()
    change_id = _insert_change()
    task_id = _insert_protocol_task()
    run_id = _insert_workflow_run()
    checkpoint_id = _insert_workflow_checkpoint()
    diary_id = _insert_session_diary()
    learning_id = _insert_learning()

    refs = {
        f"cognitive_stm:{stm_id}": "cognitive_stm",
        f"cognitive_ltm:{ltm_id}": "cognitive_ltm",
        f"memory_observation:{obs_uid}": "memory_observation",
        f"memory_event:{event_uid}": "memory_event",
        "local_context:asset-1": "local_context",
        f"commitment:{commitment_id}": "commitment",
        f"causal_edge:{edge_uid}": "causal_edge",
        f"change_log:{change_id}": "change_log",
        f"protocol_task:{task_id}": "protocol_task",
        f"workflow_run:{run_id}": "workflow_run",
        f"workflow_checkpoint:{checkpoint_id}": "workflow_checkpoint",
        f"session_diary:{diary_id}": "session_diary",
        f"learning:{learning_id}": "learning",
    }
    for ref, kind in refs.items():
        result = memory_utility.validate_memory_ref(ref)
        assert result["ok"] is True, ref
        assert result["memory_kind"] == kind
        assert memory_utility.memory_kind_for_ref(ref) == kind

    missing = memory_utility.validate_memory_ref("cognitive_stm:999999")
    assert missing == {"ok": False, "memory_kind": "cognitive_stm", "reason": "missing_ref"}


def test_memory_use_event_uid_is_idempotent_and_kind_matches_prefix(isolated_db):
    import memory_utility

    memory_ref = f"cognitive_stm:{_insert_stm()}"
    first = memory_utility.record_use_event(
        memory_ref=memory_ref,
        retrieval_trace_id="trace",
        consumer_ref="answer",
        use_stage="cited",
        outcome="helpful",
        cited_in_answer=True,
        validated_by_ref="test:one",
        evidence_refs=["b", "a"],
        query_text="question",
        now=10.0,
    )
    second = memory_utility.record_use_event(
        memory_ref=memory_ref,
        retrieval_trace_id="trace",
        consumer_ref="answer",
        use_stage="cited",
        outcome="helpful",
        cited_in_answer=True,
        validated_by_ref="test:one",
        evidence_refs=["a", "b"],
        query_text="different text should not alter uid",
        now=11.0,
    )
    assert first["event_uid"] == second["event_uid"]
    assert len(memory_utility.list_use_events(memory_ref=memory_ref)) == 1

    mismatch = memory_utility.record_use_event(
        memory_ref=memory_ref,
        memory_kind="memory_observation",
        use_stage="cited",
        outcome="helpful",
        cited_in_answer=True,
        validated_by_ref="test:mismatch",
        evidence_refs=["pytest:mismatch"],
    )
    assert mismatch["outcome"] == "unknown"
    assert mismatch["reason_code"] == "memory_kind_mismatch"
    assert mismatch["delta"] == {}


def test_query_hash_without_raw_payload_and_secret_preview(isolated_db):
    import db
    import memory_utility

    memory_ref = f"cognitive_stm:{_insert_stm()}"
    secret_query = "email francisco@example.com token=sk_live_1234567890abcdef path /Users/franciscoc/private.txt"
    event = memory_utility.record_use_event(
        memory_ref=memory_ref,
        retrieval_trace_id="trace-secret",
        use_stage="retrieved",
        outcome="unknown",
        query_text=secret_query,
        privacy_level="secret",
    )
    row = db.get_db().execute("SELECT * FROM memory_use_events WHERE event_uid=?", (event["event_uid"],)).fetchone()
    blob = json.dumps(dict(row), ensure_ascii=False)
    assert row["query_hash"]
    assert row["query_preview_redacted"] == ""
    assert "sk_live_1234567890abcdef" not in blob
    assert "francisco@example.com" not in blob
    assert "/Users/franciscoc/private.txt" not in blob

    public = memory_utility.record_use_event(
        memory_ref=memory_ref,
        retrieval_trace_id="trace-redact",
        use_stage="retrieved",
        outcome="unknown",
        query_text=secret_query,
        privacy_level="normal",
    )
    assert "[REDACTED_SECRET]" in public["query_preview_redacted"]
    assert "[REDACTED_EMAIL]" in public["query_preview_redacted"]
    assert public["redaction_applied"] is True


def test_broken_memory_ref_records_unknown_no_delta(isolated_db):
    import memory_utility

    event = memory_utility.record_use_event(
        memory_ref="cognitive_stm:999999",
        use_stage="cited",
        outcome="helpful",
        cited_in_answer=True,
        validated_by_ref="test:missing",
        evidence_refs=["pytest:missing"],
        delta={"strength": 1.0},
    )

    assert event["outcome"] == "unknown"
    assert event["reason_code"] == "missing_ref"
    assert event["delta"] == {}
    result = memory_utility.apply_memory_utility(memory_ref="cognitive_stm:999999")
    assert result["ok"] is False
    assert result["reason"] == "missing_ref"


def test_retrieved_not_used_task_done_and_thanks_are_no_delta_without_evidence(isolated_db):
    import memory_utility

    memory_ref = f"cognitive_stm:{_insert_stm(strength=0.5)}"
    memory_utility.record_use_event(
        memory_ref=memory_ref,
        retrieval_trace_id="retrieved-only",
        use_stage="retrieved",
        outcome="helpful",
        reason_code="retrieved",
    )
    memory_utility.record_use_event(
        memory_ref=memory_ref,
        retrieval_trace_id="not-used",
        use_stage="not_used",
        outcome="noise",
        reason_code="not_used",
    )
    memory_utility.record_use_event(
        memory_ref=memory_ref,
        retrieval_trace_id="task-done",
        use_stage="injected",
        outcome="helpful",
        reason_code="task_close_done",
        validated_by_ref="task_close:done",
    )
    memory_utility.record_use_event(
        memory_ref=memory_ref,
        retrieval_trace_id="thanks",
        use_stage="injected",
        outcome="helpful",
        reason_code="thanks_trust_signal",
    )

    events = memory_utility.list_use_events(memory_ref=memory_ref, limit=10)
    assert {event["reason_code"] for event in events} >= {"not_used_no_delta", "insufficient_evidence"}
    result = memory_utility.apply_memory_utility(memory_ref=memory_ref, min_samples=1, cooldown_seconds=0)
    assert result["applications"] == []


def test_task_close_helpful_requires_cited_memory_and_validated_ref(isolated_db):
    import memory_utility

    memory_ref = f"cognitive_stm:{_insert_stm(strength=0.5, stability=0.5, difficulty=0.5)}"
    memory_utility.record_use_event(
        memory_ref=memory_ref,
        retrieval_trace_id="not-cited",
        use_stage="injected",
        outcome="helpful",
        validated_by_ref="test:not-cited",
        evidence_refs=["pytest:not-cited"],
    )
    assert memory_utility.apply_memory_utility(memory_ref=memory_ref, min_samples=1, cooldown_seconds=0)["applications"] == []

    _record_helpful(memory_ref, "cited", now=20.0)
    result = memory_utility.apply_memory_utility(memory_ref=memory_ref, min_samples=1, cooldown_seconds=0, now=30.0)
    fields = {app["target_field"] for app in result["applications"]}
    assert {"strength", "stability", "difficulty"} <= fields


def test_cognitive_helpful_delta_clamped_and_idempotent(isolated_db):
    import cognitive
    import memory_utility

    stm_id = _insert_stm(strength=0.98, stability=0.99, difficulty=0.01)
    memory_ref = f"cognitive_stm:{stm_id}"
    for index in range(3):
        _record_helpful(memory_ref, str(index), now=100.0 + index)
    first = memory_utility.apply_memory_utility(memory_ref=memory_ref, min_samples=3, cooldown_seconds=0, now=200.0)
    second = memory_utility.apply_memory_utility(memory_ref=memory_ref, min_samples=3, cooldown_seconds=0, now=300.0)

    row = cognitive._get_db().execute("SELECT strength, stability, difficulty FROM stm_memories WHERE id=?", (stm_id,)).fetchone()
    assert row["strength"] == 1.0
    assert row["stability"] == 1.0
    assert row["difficulty"] == 0.0
    assert len(first["applications"]) == 3
    assert second["applications"] == []


def test_cognitive_harmful_sets_under_review_not_delete(isolated_db):
    import cognitive
    import memory_utility

    ltm_id = _insert_ltm(strength=0.5, difficulty=0.5, tags="")
    memory_ref = f"cognitive_ltm:{ltm_id}"
    _record_harmful(memory_ref, "one", now=10.0)
    result = memory_utility.apply_memory_utility(memory_ref=memory_ref, min_samples=3, cooldown_seconds=0, now=20.0)

    row = cognitive._get_db().execute("SELECT strength, difficulty, tags FROM ltm_memories WHERE id=?", (ltm_id,)).fetchone()
    assert row is not None
    assert row["strength"] < 0.5
    assert row["difficulty"] > 0.5
    assert "under_review" in row["tags"]
    assert {app["target_field"] for app in result["applications"]} >= {"strength", "difficulty", "tags"}


def test_observation_delta_uses_observation_fields_not_difficulty(isolated_db):
    import db
    import memory_utility

    obs_uid = _insert_observation("obs-helpful")
    memory_ref = f"memory_observation:{obs_uid}"
    for index in range(3):
        _record_helpful(memory_ref, str(index), now=100.0 + index)

    result = memory_utility.apply_memory_utility(memory_ref=memory_ref, min_samples=3, cooldown_seconds=0, now=200.0)
    fields = {app["target_field"] for app in result["applications"]}
    assert fields == {"confidence", "salience", "stability"}
    assert "difficulty" not in _cols(db.get_db(), "memory_observations")
    row = db.get_db().execute("SELECT salience, confidence, stability FROM memory_observations WHERE observation_uid=?", (obs_uid,)).fetchone()
    assert row["salience"] > 0.5
    assert row["confidence"] > 0.5
    assert row["stability"] > 0.5


def test_pinned_or_critical_memory_not_penalized_for_non_use(isolated_db):
    import cognitive
    import memory_utility

    stm_id = _insert_stm(strength=0.5, lifecycle_state="pinned")
    memory_ref = f"cognitive_stm:{stm_id}"
    _record_harmful(memory_ref, "pin", now=10.0)
    result = memory_utility.apply_memory_utility(memory_ref=memory_ref, min_samples=1, cooldown_seconds=0, now=20.0)
    row = cognitive._get_db().execute("SELECT strength FROM stm_memories WHERE id=?", (stm_id,)).fetchone()
    assert row["strength"] == 0.5
    assert all(item["reason"] == "protected_memory" for item in result["suppressed"] if item.get("target_field") in {"strength", "difficulty"})


def test_local_context_and_causal_edge_feedback_do_not_edit_content(isolated_db):
    import db
    import memory_utility

    local_ref = "local_context:asset-abc"
    _record_harmful(local_ref, "local", now=10.0)
    local_result = memory_utility.apply_memory_utility(memory_ref=local_ref, min_samples=1, cooldown_seconds=0, now=20.0)
    assert local_result["applications"][0]["applied"] is False
    assert local_result["applications"][0]["metadata"]["content_edit"] is False

    edge_uid = _insert_causal_edge()
    edge_ref = f"causal_edge:{edge_uid}"
    _record_harmful(edge_ref, "edge", now=30.0)
    edge_result = memory_utility.apply_memory_utility(memory_ref=edge_ref, min_samples=1, cooldown_seconds=0, now=40.0)
    assert edge_result["applications"][0]["applied"] is False
    assert edge_result["applications"][0]["metadata"]["route"] == "causal_graph"
    assert db.get_db().execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='causal_edges'").fetchone() is None


def test_min_samples_cooldown_daily_limit_and_bridge_exactly_once(isolated_db):
    import db
    import memory_utility

    memory_ref = f"cognitive_stm:{_insert_stm(strength=0.5, stability=0.5, difficulty=0.5)}"
    _record_helpful(memory_ref, "one", now=10.0)
    assert memory_utility.apply_memory_utility(memory_ref=memory_ref, min_samples=3, cooldown_seconds=0, now=20.0)["applications"] == []

    _record_helpful(memory_ref, "two", now=11.0)
    _record_helpful(memory_ref, "three", now=12.0)
    first = memory_utility.apply_memory_utility(
        memory_ref=memory_ref,
        min_samples=3,
        cooldown_seconds=86400,
        max_daily_abs_delta=0.04,
        now=100.0,
    )
    assert first["applications"]
    assert sum(abs(app["delta"]) for app in first["applications"]) <= 0.04

    for index in range(3, 6):
        _record_helpful(memory_ref, str(index), now=200.0 + index)
    cooled = memory_utility.apply_memory_utility(memory_ref=memory_ref, min_samples=3, cooldown_seconds=86400, now=300.0)
    assert cooled["applications"] == []
    assert any(item["reason"] == "cooldown" for item in cooled["suppressed"])

    bridges = db.get_db().execute(
        "SELECT event_uid, memory_ref, target_field, policy_version, COUNT(*) c "
        "FROM memory_utility_application_events GROUP BY event_uid, memory_ref, target_field, policy_version HAVING c > 1"
    ).fetchall()
    assert bridges == []


def test_cooldown_does_not_split_fields_in_same_batch(isolated_db):
    import memory_utility

    memory_ref = f"cognitive_stm:{_insert_stm(strength=0.5, stability=0.5, difficulty=0.5)}"
    for index in range(3):
        _record_helpful(memory_ref, str(index), now=100.0 + index)
    first = memory_utility.apply_memory_utility(
        memory_ref=memory_ref,
        min_samples=3,
        cooldown_seconds=86400,
        max_daily_abs_delta=0.15,
        now=200.0,
    )
    assert {app["target_field"] for app in first["applications"]} == {"strength", "stability", "difficulty"}

    for index in range(3, 6):
        _record_helpful(memory_ref, str(index), now=300.0 + index)
    second = memory_utility.apply_memory_utility(
        memory_ref=memory_ref,
        min_samples=3,
        cooldown_seconds=86400,
        max_daily_abs_delta=0.30,
        now=400.0,
    )
    assert second["applications"] == []
    assert any(item["reason"] == "cooldown" for item in second["suppressed"])


def test_application_batch_records_old_new_delta_event_uids_and_policy(isolated_db):
    import memory_utility

    memory_ref = f"cognitive_stm:{_insert_stm(strength=0.5)}"
    for index in range(3):
        _record_helpful(memory_ref, str(index), now=100 + index)
    result = memory_utility.apply_memory_utility(memory_ref=memory_ref, min_samples=3, cooldown_seconds=0, now=200.0)
    strength_app = next(app for app in result["applications"] if app["target_field"] == "strength")
    assert strength_app["policy_version"] == memory_utility.POLICY_VERSION
    assert strength_app["old_value"] == 0.5
    assert strength_app["new_value"] > 0.5
    assert strength_app["delta"] > 0
    assert len(strength_app["event_uids"]) == 3


def test_rollback_when_correction_rate_rises(isolated_db):
    import cognitive
    import memory_utility

    stm_id = _insert_stm(strength=0.5)
    memory_ref = f"cognitive_stm:{stm_id}"
    for index in range(3):
        _record_helpful(memory_ref, str(index), now=100 + index)
    memory_utility.apply_memory_utility(memory_ref=memory_ref, min_samples=3, cooldown_seconds=0, now=200.0)
    raised = cognitive._get_db().execute("SELECT strength FROM stm_memories WHERE id=?", (stm_id,)).fetchone()["strength"]
    assert raised > 0.5

    rollback = memory_utility.rollback_applications(memory_ref=memory_ref, rollback_ref="correction:spike")
    row = cognitive._get_db().execute("SELECT strength FROM stm_memories WHERE id=?", (stm_id,)).fetchone()
    assert row["strength"] == 0.5
    assert rollback["rolled_back"]


def test_explain_score_change_uses_refs_not_payload(isolated_db):
    import memory_utility

    memory_ref = f"cognitive_stm:{_insert_stm(strength=0.5)}"
    for index in range(3):
        _record_helpful(memory_ref, str(index), now=100 + index)
    memory_utility.apply_memory_utility(memory_ref=memory_ref, min_samples=3, cooldown_seconds=0, now=200.0)
    explanation = memory_utility.explain_score_change(memory_ref=memory_ref)
    blob = json.dumps(explanation, ensure_ascii=False)

    assert "memory_use_event:" in blob
    assert "How to use memory" not in blob
    assert "reason_code" in blob
