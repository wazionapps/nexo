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


def test_record_router_usage_records_source_stats_with_budget_fields():
    payload = {
        "ok": True,
        "intent": "file_location",
        "should_inject": False,
        "evidence_refs": [],
        "budget_tier": "quick",
        "budget_decision_uid": "bd-source",
        "policy_version": "runtime_budget_v1",
        "first_response_deadline_ms": 300,
        "source_stats": [
            {
                "source": "project_atlas",
                "phase": "primary",
                "ok": True,
                "elapsed_ms": 12,
                "result_count": 0,
                "evidence_refs_count": 0,
                "aborted_reason": "",
            }
        ],
        "runtime_budget_policy": {
            "budget_tier": "quick",
            "budget_decision_uid": "bd-source",
            "policy_version": "runtime_budget_v1",
            "surface": "pre_answer",
            "risk_level": "low",
            "allowed_sources": ["project_atlas"],
        },
    }

    result = usage_events.record_router_usage("where is file", payload, elapsed_ms=20, deadline_ms=300)
    events = usage_events.list_recent_events(limit=5)

    assert result["ok"] is True
    assert events[0]["source"] == "pre_answer_router"
    assert any(event["source"] == "project_atlas" and event["budget_tier"] == "quick" for event in events)


def test_usage_events_fresh_schema_has_budget_columns(isolated_db):
    query = "release status"
    result = usage_events.record_usage_event(
        query=query,
        source="pre_answer_router",
        budget_tier="critical",
        budget_decision_uid="bd1",
        policy_version="runtime_budget_v1",
        surface="pre_answer",
        risk_level="critical",
        first_response_deadline_ms=1500,
        required_sources_count=3,
        missing_required_sources_count=1,
        optional_sources_skipped_count=2,
        gap_disclosed=True,
        privacy_level="private",
    )

    assert result["ok"] is True
    event = result["event"]
    assert event["budget_tier"] == "critical"
    assert event["missing_required_sources_count"] == 1


def test_usage_events_legacy_schema_gets_budget_columns(tmp_path):
    usage_db = tmp_path / "legacy_usage.db"
    conn = sqlite3.connect(usage_db)
    conn.execute(
        f"""
        CREATE TABLE {usage_events.USAGE_TABLE} (
            event_id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            client TEXT NOT NULL DEFAULT '',
            tool TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'local_context',
            route_stage TEXT NOT NULL DEFAULT '',
            intent TEXT NOT NULL DEFAULT '',
            query_hash TEXT NOT NULL DEFAULT '',
            elapsed_ms INTEGER NOT NULL DEFAULT 0,
            deadline_ms INTEGER NOT NULL DEFAULT 0,
            timed_out INTEGER NOT NULL DEFAULT 0,
            result_count INTEGER NOT NULL DEFAULT 0,
            should_inject INTEGER NOT NULL DEFAULT 0,
            injected_chars INTEGER NOT NULL DEFAULT 0,
            evidence_refs_count INTEGER NOT NULL DEFAULT 0,
            aborted_reason TEXT NOT NULL DEFAULT '',
            used_before_response INTEGER NOT NULL DEFAULT 0,
            index_count INTEGER NOT NULL DEFAULT 0,
            index_phase TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{{}}'
        )
        """
    )
    conn.commit()
    conn.close()

    result = usage_events.record_usage_event(
        query="legacy",
        budget_tier="quick",
        policy_version="runtime_budget_v1",
        db_path=usage_db,
    )

    assert result["ok"] is True
    conn = sqlite3.connect(usage_db)
    conn.row_factory = sqlite3.Row
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({usage_events.USAGE_TABLE})").fetchall()}
    row = conn.execute(f"SELECT budget_tier, policy_version FROM {usage_events.USAGE_TABLE}").fetchone()
    conn.close()
    assert "budget_tier" in columns
    assert "gap_disclosed" in columns
    assert row["budget_tier"] == "quick"
    assert row["policy_version"] == "runtime_budget_v1"


def test_usage_events_metadata_blocks_raw_payload_keys(isolated_db):
    secret = "sk_live_1234567890abcdef"
    result = usage_events.record_usage_event(
        query=f"private {secret}",
        metadata={
            "query": f"private {secret}",
            "query_preview": "preview",
            "payload": {"text": "raw private"},
            "messages": ["raw"],
            "safe": "ok",
        },
    )

    assert result["ok"] is True
    rows = usage_events.list_recent_events(limit=1)
    metadata = json.loads(rows[0]["metadata_json"])
    assert metadata["query"] == "[redacted]"
    assert metadata["query_preview"] == "[redacted]"
    assert metadata["payload"] == "[redacted]"
    assert metadata["messages"] == "[redacted]"
    assert metadata["safe"] == "ok"
    assert secret not in rows[0]["metadata_json"]


def test_usage_events_observatory_metrics_by_tier_and_source(isolated_db):
    usage_events.record_usage_event(
        source="project_atlas",
        budget_tier="quick",
        elapsed_ms=10,
        timed_out=False,
        injected_chars=0,
        created_at=1000.0,
    )
    usage_events.record_usage_event(
        source="project_atlas",
        budget_tier="quick",
        elapsed_ms=30,
        timed_out=True,
        injected_chars=100,
        created_at=1001.0,
    )
    usage_events.record_usage_event(
        source="evidence_ledger",
        budget_tier="critical",
        elapsed_ms=100,
        timed_out=True,
        missing_required_sources_count=1,
        created_at=1002.0,
    )

    summary = usage_events.summarize_usage(window_seconds=86400, now_ts=1100.0)
    quick = summary["runtime_budget_metrics"]["by_tier"]["quick"]
    source = summary["runtime_budget_metrics"]["by_tier_source"]["quick"]["project_atlas"]

    assert quick["sample_count"] == 2
    assert quick["p50_elapsed_ms"] == 10
    assert quick["p95_elapsed_ms"] == 30
    assert quick["timeout_rate"] == 0.5
    assert source["sample_count"] == 2
    assert summary["runtime_budget_metrics"]["critical_missing_required_count"] == 1


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
