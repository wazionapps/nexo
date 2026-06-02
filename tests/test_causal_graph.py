from __future__ import annotations

import json
import time


def _indexes(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA index_list({table})").fetchall()}


def test_causal_graph_reuses_existing_kg_edges_no_active_causal_edges_table(isolated_db):
    import causal_graph
    import db

    causal_graph.ensure_kg_indexes()
    conn = db.get_db()
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='causal_edge_candidates'").fetchone()
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='causal_edges'").fetchone() is None

    kg_conn = causal_graph._kg_db()
    assert kg_conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kg_edges'").fetchone()
    assert kg_conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='causal_edges'").fetchone() is None


def test_causal_edge_candidates_schema_and_indexes(isolated_db):
    import db

    conn = db.get_db()
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(causal_edge_candidates)").fetchall()}
    assert {
        "candidate_uid",
        "source_type",
        "source_ref",
        "relation",
        "target_type",
        "target_ref",
        "reason_public",
        "evidence_refs_json",
        "source_event_uid",
        "producer",
        "project_key",
        "privacy_level",
        "confidence",
        "status",
        "review_reason",
        "promoted_edge_uid",
        "metadata_json",
    } <= cols
    assert {
        "idx_causal_candidates_status_updated",
        "idx_causal_candidates_source",
        "idx_causal_candidates_target",
        "idx_causal_candidates_project",
    } <= _indexes(conn, "causal_edge_candidates")


def test_causal_candidate_uid_is_deterministic_and_idempotent(isolated_db):
    import causal_graph

    first = causal_graph.propose_candidate(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="Router change verified by test.",
        evidence_refs=["pytest:test_pre_answer_router"],
        producer="deep_sleep",
        project_key="nexo",
        now=1000.0,
    )
    second = causal_graph.propose_candidate(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="Same edge with a different reason should update.",
        evidence_refs=["pytest:test_pre_answer_router"],
        producer="deep_sleep",
        project_key="nexo",
        now=1010.0,
    )

    assert first["candidate_uid"] == second["candidate_uid"]
    assert len(causal_graph.list_candidates(limit=10)) == 1
    assert second["updated_at"] == 1010.0


def test_kg_composite_indexes_for_causal_queries(isolated_db):
    import causal_graph

    causal_graph.ensure_kg_indexes()
    kg_conn = causal_graph._kg_db()
    assert {
        "idx_kg_edges_source_relation_active",
        "idx_kg_edges_target_relation_active",
        "idx_kg_edges_relation_active",
    } <= _indexes(kg_conn, "kg_edges")


def test_causal_edge_rejects_unknown_relation(isolated_db):
    import causal_graph

    result = causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:unknown",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="bad relation",
        evidence_refs=["pytest:test"],
    )

    assert result == {"ok": False, "status": "rejected", "review_reason": "unknown_relation"}


def test_causal_edge_requires_evidence_refs_for_active(isolated_db):
    import causal_graph

    result = causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="No evidence refs",
        evidence_refs=[],
    )

    assert result["ok"] is False
    assert result["review_reason"] == "missing_evidence"


def test_causal_edge_uid_is_idempotent(isolated_db):
    import causal_graph

    first = causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="Verified by router tests.",
        evidence_refs=["pytest:test_pre_answer_router"],
        project_key="nexo",
        confidence=0.95,
    )
    second = causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="Reason wording changed but evidence identity did not.",
        evidence_refs=["pytest:test_pre_answer_router"],
        project_key="nexo",
        confidence=0.95,
    )

    assert first["ok"] is True
    assert second["action"] == "NOOP"
    assert first["edge_uid"] == second["edge_uid"]


def test_causal_edge_direction_verified_by_action_to_test(isolated_db):
    import causal_graph

    causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="The action is verified by the test, not the reverse.",
        evidence_refs=["pytest:test_pre_answer_router"],
        project_key="nexo",
        confidence=0.95,
    )
    result = causal_graph.query_edges(ref_type="file", ref="src/pre_answer_router.py", project_key="nexo")

    assert result["has_evidence"] is True
    edge = result["edges"][0]
    assert edge["source_type"] == "file"
    assert edge["source_ref"] == "src/pre_answer_router.py"
    assert edge["relation"] == "causal:verified_by"
    assert edge["target_type"] == "test"


def test_causal_edge_dangling_ref_goes_to_review_not_active(isolated_db):
    import causal_graph

    result = causal_graph.upsert_active_edge(
        source_type="protocol_task",
        source_ref="PT-MISSING",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="Missing task should not auto-create node.",
        evidence_refs=["pytest:test"],
    )

    assert result["ok"] is False
    assert result["status"] == "review"
    assert result["review_reason"] == "source_missing_ref"
    assert causal_graph._kg().get_node("protocol_task", "PT-MISSING") is None


def test_causal_graph_validates_refs_before_kg_upsert(isolated_db):
    import causal_graph

    ok, reason = causal_graph.validate_ref("file", "src/pre_answer_router.py", evidence_refs=[])
    assert ok is True
    assert reason == ""

    ok, reason = causal_graph.validate_ref("unknown_type", "x", evidence_refs=["evidence:x"])
    assert ok is False
    assert reason == "unsupported_ref_type"


def test_task_close_creates_task_produced_change_log_edge_once(isolated_db):
    import causal_graph
    import db

    task = db.create_protocol_task("sid", "Fix causal graph", task_type="execute")
    change = db.log_change("sid", "src/causal_graph.py", "Add causal graph", "Spec 02")
    first = causal_graph.record_task_close_edges(
        task_id=task["task_id"],
        change_log_id=change["id"],
        evidence_refs=[f"change_log:{change['id']}"],
        project_key="nexo",
    )
    second = causal_graph.record_task_close_edges(
        task_id=task["task_id"],
        change_log_id=change["id"],
        evidence_refs=[f"change_log:{change['id']}"],
        project_key="nexo",
    )

    assert first[0]["ok"] is True
    assert second[0]["action"] == "NOOP"
    query = causal_graph.query_edges(ref_type="protocol_task", ref=task["task_id"], project_key="nexo")
    assert any(edge["relation"] == "ops:produced" for edge in query["edges"])


def test_task_close_creates_verified_by_edge_from_test_evidence(isolated_db):
    import causal_graph
    import db

    task = db.create_protocol_task("sid", "Verify causal graph", task_type="execute")
    results = causal_graph.record_task_close_edges(
        task_id=task["task_id"],
        test_refs=["tests/test_causal_graph.py"],
        evidence_refs=["pytest:test_causal_graph"],
        project_key="nexo",
    )

    assert any(result.get("ok") for result in results)
    query = causal_graph.query_edges(ref_type="protocol_task", ref=task["task_id"], project_key="nexo")
    assert any(edge["relation"] == "causal:verified_by" for edge in query["edges"])


def test_commitment_resolution_creates_resolved_by_edge(isolated_db):
    import causal_graph
    import db

    created = db.create_commitment(
        statement="Ship causal graph",
        source_type="test",
        source_id="commitment-resolution",
        session_id="sid",
        project_key="nexo",
        action_ref_type="file",
        action_ref_id="src/causal_graph.py",
        evidence_ref="tests/test_causal_graph.py",
        dedupe_key="commitment-resolution",
    )
    db.update_commitment_status(created["id"], status="fulfilled", evidence_ref="tests/test_causal_graph.py")
    results = causal_graph.record_commitment_resolution_edges(created["id"])

    assert any(result.get("ok") for result in results)
    query = causal_graph.query_edges(ref_type="commitment", ref=created["id"], project_key="nexo")
    assert any(edge["relation"] == "causal:resolved_by" for edge in query["edges"])


def test_commitment_missed_cancelled_superseded_do_not_create_resolved_by(isolated_db):
    import causal_graph
    import db

    created = db.create_commitment(
        statement="Do not ship",
        source_type="test",
        source_id="commitment-missed",
        session_id="sid",
        project_key="nexo",
        action_ref_type="file",
        action_ref_id="src/causal_graph.py",
        evidence_ref="tests/test_causal_graph.py",
        dedupe_key="commitment-missed",
    )
    db.update_commitment_status(created["id"], status="missed")

    assert causal_graph.record_commitment_resolution_edges(created["id"]) == []
    query = causal_graph.query_edges(ref_type="commitment", ref=created["id"], project_key="nexo")
    assert query["has_evidence"] is False


def test_deep_sleep_candidate_does_not_create_active_kg_edge(isolated_db):
    import causal_graph

    candidate = causal_graph.propose_candidate(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="Deep Sleep proposed this edge.",
        evidence_refs=["deep_sleep:1"],
        producer="deep_sleep",
        project_key="nexo",
    )

    assert candidate["status"] == "proposed"
    assert causal_graph.query_edges(ref_type="file", ref="src/pre_answer_router.py", project_key="nexo")["has_evidence"] is False


def test_memory_executive_proposed_edge_stays_candidate_until_promoted(isolated_db):
    import causal_graph

    event = {
        "event_uid": "ev-causal",
        "project_key": "nexo",
        "privacy_level": "normal",
        "evidence_refs": ["memory_event:ev-causal"],
        "metadata": {
            "causal_edge": {
                "source_type": "file",
                "source_ref": "src/pre_answer_router.py",
                "relation": "causal:verified_by",
                "target_type": "test",
                "target_ref": "tests/test_pre_answer_router.py",
                "reason_public": "Memory Executive proposed a causal edge.",
                "evidence_refs": ["memory_event:ev-causal"],
                "confidence": 0.82,
            }
        },
    }
    decision = {"decision_kind": "proposed_causal_edge", "dedupe_key": "causal:1", "confidence": 0.82}
    candidate = causal_graph.propose_from_memory_executive(event, decision)

    assert candidate["ok"] is True
    assert candidate["producer"] == "memory_executive"
    assert candidate["status"] == "proposed"
    assert causal_graph.query_edges(ref_type="file", ref="src/pre_answer_router.py", project_key="nexo")["has_evidence"] is False


def test_sensitive_edge_redacts_reason_public(isolated_db):
    import causal_graph

    result = causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="Sensitive token=sk_live_1234567890abcdef reason",
        evidence_refs=["pytest:sensitive"],
        privacy_level="sensitive",
        project_key="nexo",
    )

    assert result["ok"] is True
    props = result["properties"]
    assert "sk_live_1234567890abcdef" not in props["reason_public"]
    assert props["redaction_applied"] is True


def test_secret_edge_is_rejected_or_reference_only(isolated_db):
    import causal_graph

    result = causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="secret",
        evidence_refs=["secret:ref"],
        privacy_level="secret",
        project_key="nexo",
    )

    assert result["ok"] is False
    assert result["review_reason"] == "secret_reference_only"


def test_private_edge_not_rendered_cross_project(isolated_db):
    import causal_graph

    causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="Private project reason",
        evidence_refs=["pytest:private"],
        privacy_level="private",
        project_key="nexo",
    )

    assert causal_graph.query_edges(ref_type="file", ref="src/pre_answer_router.py", project_key="other")["has_evidence"] is False


def test_causal_answer_asks_permission_without_revealing_sensitive_payload(isolated_db):
    import causal_graph

    causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="Client ACME private lawsuit detail",
        evidence_refs=["pytest:sensitive-answer"],
        privacy_level="sensitive",
        project_key="nexo",
    )
    rendered = causal_graph.render_query_result(
        causal_graph.query_edges(ref_type="file", ref="src/pre_answer_router.py", project_key="nexo")
    )

    assert "permiso" in rendered
    assert "ACME" not in rendered
    assert "lawsuit" not in rendered


def test_no_edge_returns_no_evidence_instead_of_guess(isolated_db):
    import causal_graph

    result = causal_graph.query_edges(ref_type="file", ref="src/pre_answer_router.py", project_key="nexo")
    assert result["has_evidence"] is False
    assert result["message"] == "no tengo evidencia suficiente"
    assert causal_graph.render_query_result(result) == "No tengo evidencia suficiente."


def test_contradicted_edge_not_used_as_positive_answer(isolated_db):
    import causal_graph

    causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="Contradicted edge",
        evidence_refs=["pytest:contradicted"],
        status="contradicted",
        project_key="nexo",
    )

    assert causal_graph.query_edges(ref_type="file", ref="src/pre_answer_router.py", project_key="nexo")["has_evidence"] is False


def test_stale_edge_only_returned_when_historical_requested(isolated_db):
    import causal_graph

    causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="Stale edge",
        evidence_refs=["pytest:stale"],
        status="stale",
        project_key="nexo",
    )

    assert causal_graph.query_edges(ref_type="file", ref="src/pre_answer_router.py", project_key="nexo")["has_evidence"] is False
    assert causal_graph.query_edges(
        ref_type="file",
        ref="src/pre_answer_router.py",
        project_key="nexo",
        include_historical=True,
    )["has_evidence"] is True


def test_causal_query_file_and_release_under_latency_budget(isolated_db):
    import causal_graph

    causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="Fast lookup edge",
        evidence_refs=["pytest:fast"],
        project_key="nexo",
    )

    started = time.perf_counter()
    for _ in range(25):
        causal_graph.query_edges(ref_type="file", ref="src/pre_answer_router.py", project_key="nexo")
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 100
