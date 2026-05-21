from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from local_context import usage_events


def test_usage_events_store_outside_sidecar_and_hashes_query(isolated_db):
    query = "donde esta el contrato secreto token abc123"
    result = usage_events.record_usage_event(
        query=query,
        client="codex",
        tool="nexo_context_router",
        intent="file_location",
        route_stage="pre_answer",
        elapsed_ms=42,
        deadline_ms=800,
        result_count=2,
        should_inject=True,
        injected_chars=1200,
        evidence_refs_count=2,
        used_before_response=True,
        index_count=17,
        index_phase="initial_indexing",
        metadata={"password": "1234", "safe": "ok", "note": "key sk_live_1234567890abcdef must not persist"},
    )

    usage_path = usage_events.usage_db_path()
    sidecar_path = Path(isolated_db["local_context_db"])
    assert result["ok"] is True
    assert usage_path.name == "test_local_context_usage.db"
    assert usage_path != sidecar_path
    assert usage_path.exists()

    conn = sqlite3.connect(usage_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(f"SELECT * FROM {usage_events.USAGE_TABLE}").fetchone()
    conn.close()

    assert row["query_hash"] == usage_events.hash_query(query)
    assert row["client"] == "codex"
    assert row["tool"] == "nexo_context_router"
    assert row["used_before_response"] == 1
    assert row["index_count"] == 17
    assert row["index_phase"] == "initial_indexing"
    metadata = json.loads(row["metadata_json"])
    assert metadata["password"] == "[redacted]"
    assert metadata["safe"] == "ok"
    assert "sk_live_1234567890abcdef" not in row["metadata_json"]
    assert query.encode("utf-8") not in usage_path.read_bytes()


def test_usage_snapshot_distinguishes_indexed_from_used_before_response():
    empty = usage_events.usage_snapshot(indexed_files=12, index_phase="initial_indexing")
    assert empty["status"] == "indexed_not_used"
    assert empty["indexed"]["files_found"] == 12
    assert empty["used_before_response"]["events"] == 0

    usage_events.record_usage_event(
        query="factura bmw maria",
        intent="answer",
        used_before_response=True,
        should_inject=True,
        result_count=1,
        evidence_refs_count=1,
    )

    active = usage_events.usage_snapshot(indexed_files=12, index_phase="initial_indexing")
    assert active["status"] == "indexed_and_used"
    assert active["used_before_response"]["events"] == 1
    assert active["usage"]["injected_events"] == 1
    assert active["usage"]["by_source"]["local_context"] == 1
    assert active["usage"]["by_route_stage"][""] == 1


def test_record_router_usage_extracts_injection_metrics():
    payload = {
        "ok": True,
        "intent": "modify_existing",
        "should_inject": True,
        "rendered": "LOCAL CONTEXT EVIDENCE\n- file",
        "evidence_refs": ["local_asset:a#chunk:c"],
        "truncated": False,
    }

    result = usage_events.record_router_usage(
        "modifica el archivo existente",
        payload,
        client="desktop",
        tool="preAnswerLocalContext",
        elapsed_ms=77,
        deadline_ms=1000,
    )

    assert result["ok"] is True
    event = result["event"]
    assert event["intent"] == "modify_existing"
    assert event["route_stage"] == "pre_answer"
    assert event["elapsed_ms"] == 77
    assert event["deadline_ms"] == 1000
    assert event["should_inject"] is True
    assert event["injected_chars"] == len(payload["rendered"])
    assert event["evidence_refs_count"] == 1
    assert event["used_before_response"] is True


def test_record_router_usage_marks_deadline_abortions_as_timeouts():
    result = usage_events.record_router_usage(
        "donde esta el archivo",
        {
            "ok": True,
            "intent": "file_location",
            "should_inject": False,
            "aborted_reason": "deadline_exhausted",
            "evidence_refs": [],
        },
        client="desktop",
        tool="preAnswerLocalContext",
        elapsed_ms=2500,
        deadline_ms=2500,
    )

    assert result["ok"] is True
    assert result["event"]["timed_out"] is True


def test_pre_answer_local_context_shadow_records_without_inject(monkeypatch):
    import local_context.api as local_context_api
    import pre_answer_router as router

    monkeypatch.setenv("NEXO_PRE_ANSWER_LOCAL_CONTEXT_MODE", "shadow")

    def fake_context_router(query, *, intent, limit, current_context, max_chars):
        return {
            "ok": True,
            "query": query,
            "intent": intent,
            "should_inject": True,
            "rendered": "LOCAL CONTEXT EVIDENCE\n- useful local file",
            "evidence_refs": ["local_asset:a#chunk:c"],
        }

    monkeypatch.setattr(local_context_api, "context_router", fake_context_router)

    result = router._source_local_context(
        router.SourceRequest(
            query="donde esta el archivo local",
            intent="file_location",
            max_chars=900,
        )
    )
    events = usage_events.list_recent_events(limit=5)

    assert result.skipped is True
    assert result.aborted_reason == "shadow_no_inject"
    assert result.has_evidence is False
    assert events
    assert events[0]["route_stage"] == "pre_answer:shadow"
    assert events[0]["intent"] == "file_location"
    assert events[0]["should_inject"] == 0


def test_pre_answer_local_context_adaptive_skip_records_event(monkeypatch):
    import local_context.api as local_context_api
    import pre_answer_router as router

    def fail_context_router(*_args, **_kwargs):
        raise AssertionError("adaptive skip should not query local context")

    monkeypatch.setattr(local_context_api, "context_router", fail_context_router)

    result = router._source_local_context(
        router.SourceRequest(
            query="hola, gracias",
            intent="prior_work",
            max_chars=900,
        )
    )
    events = usage_events.list_recent_events(limit=5)

    assert result.skipped is True
    assert result.aborted_reason == "adaptive_skip"
    assert events[0]["route_stage"] == "pre_answer:inject"
    assert events[0]["aborted_reason"] == "adaptive_skip"
