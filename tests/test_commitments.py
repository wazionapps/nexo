from __future__ import annotations


def test_commitment_ledger_dedupes_and_resolves_by_evidence(isolated_db):
    import db

    first = db.create_commitment(
        statement="Revisar benchmark CAS antes del release Brain",
        session_id="nexo-commitment",
        source_type="assistant_text",
        source_id="assistant-1",
        project_key="nexo",
        confidence=0.8,
    )
    second = db.create_commitment(
        statement="Revisar benchmark CAS antes del release Brain",
        session_id="nexo-commitment",
        source_type="assistant_text",
        source_id="assistant-1",
        project_key="nexo",
        confidence=0.8,
    )

    rows = db.list_commitments(query="benchmark CAS release", status="open", session_id="nexo-commitment")
    resolved = db.resolve_matching_commitments(
        session_id="nexo-commitment",
        evidence_text="Benchmark CAS revisado antes del release Brain con tests verdes",
        action_ref_type="protocol_task",
        action_ref_id="PT-CAS",
        evidence_ref="protocol_task:PT-CAS",
    )
    closed = db.list_commitments(status="closed", session_id="nexo-commitment")

    assert first["ok"] is True
    assert first["created"] is True
    assert second["ok"] is True
    assert second["created"] is False
    assert [row["id"] for row in rows] == [first["id"]]
    assert resolved["resolved"] == 1
    assert closed[0]["id"] == first["id"]
    assert closed[0]["status"] == "fulfilled"
    assert closed[0]["evidence_ref"] == "protocol_task:PT-CAS"


def test_r17_detected_promise_creates_commitment_and_marks_progress(isolated_db):
    import db
    from enforcement_engine import HeadlessEnforcer

    enforcer = HeadlessEnforcer()
    enforcer.set_session_id("nexo-r17-commitment")
    enforcer.on_assistant_text_r17(
        "Voy a revisar el benchmark CAS antes de publicar Brain.",
        promise_detector=lambda _text: True,
    )

    created = db.list_commitments(query="benchmark CAS", status="open", session_id="nexo-r17-commitment")
    enforcer.on_tool_call("Bash", {"cmd": "pytest tests/test_pre_answer_router.py"})
    updated = db.list_commitments(query="benchmark CAS", session_id="nexo-r17-commitment")

    assert len(created) == 1
    assert created[0]["source_type"] == "assistant_text"
    assert created[0]["status"] == "active"
    assert updated[0]["status"] == "in_progress"
    assert updated[0]["evidence_ref"] == "tool:Bash"


def test_commitment_resolution_does_not_close_on_weak_generic_overlap(isolated_db):
    import db

    created = db.create_commitment(
        statement="Revisar benchmark CAS antes del release Brain",
        session_id="nexo-weak-close",
        source_type="assistant_text",
        source_id="assistant-weak",
        project_key="nexo",
        confidence=0.8,
    )

    resolved = db.resolve_matching_commitments(
        session_id="nexo-weak-close",
        evidence_text="Release Brain",
        action_ref_type="protocol_task",
        action_ref_id="PT-RELEASE",
        evidence_ref="protocol_task:PT-RELEASE",
    )
    open_rows = db.list_commitments(status="open", session_id="nexo-weak-close")

    assert created["ok"] is True
    assert resolved["resolved"] == 0
    assert [row["id"] for row in open_rows] == [created["id"]]


def test_commitment_resolution_closes_on_strong_evidence(isolated_db):
    import db

    created = db.create_commitment(
        statement="Revisar benchmark CAS antes del release Brain",
        session_id="nexo-strong-close",
        source_type="assistant_text",
        source_id="assistant-strong",
        project_key="nexo",
        confidence=0.8,
    )

    resolved = db.resolve_matching_commitments(
        session_id="nexo-strong-close",
        evidence_text="Benchmark CAS revisado antes del release Brain con tests verdes",
        action_ref_type="protocol_task",
        action_ref_id="PT-CAS",
        evidence_ref="protocol_task:PT-CAS",
    )
    closed = db.list_commitments(status="closed", session_id="nexo-strong-close")

    assert created["ok"] is True
    assert resolved["resolved"] == 1
    assert closed[0]["id"] == created["id"]
    assert closed[0]["metadata"]["resolved_by"] == "strong_matching_evidence"


def test_commitment_search_filters_after_bounded_recall_window_not_visible_limit(isolated_db):
    import db

    target = db.create_commitment(
        statement="Revisar benchmark CAS antes del release Brain",
        session_id="nexo-recall-window",
        source_type="assistant_text",
        source_id="target",
        project_key="nexo",
        created_at=1.0,
    )
    for index in range(30):
        db.create_commitment(
            statement=f"Commitment distractor {index}",
            session_id="nexo-recall-window",
            source_type="assistant_text",
            source_id=f"distractor-{index}",
            project_key="nexo",
            created_at=10.0 + index,
        )

    rows = db.list_commitments(
        query="benchmark CAS",
        status="open",
        session_id="nexo-recall-window",
        limit=6,
    )

    assert [row["id"] for row in rows] == [target["id"]]
