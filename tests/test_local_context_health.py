from __future__ import annotations

import sqlite3
import time

from local_context import api
from local_context import health
from local_context import usage_events


def test_sidecar_index_snapshot_reads_readonly_without_recording_usage():
    api.ensure_ready()
    conn = api.get_local_context_db()
    conn.execute(
        """
        INSERT OR IGNORE INTO local_assets(
            asset_id, root_id, volume_id, path, display_path, parent_path, file_type,
            extension, size_bytes, quick_fingerprint, status, first_seen_at, last_seen_at, updated_at
        )
        VALUES ('asset_health', NULL, 'local', '/tmp/health.txt', '/tmp/health.txt',
                '/tmp', 'text', '.txt', 10, 'fp', 'active', 1, 1, 1)
        """
    )
    conn.commit()
    before_queries = conn.execute("SELECT COUNT(*) AS total FROM local_context_queries").fetchone()["total"]

    result = health.sidecar_index_snapshot(deadline_ms=300, db_timeout_ms=80)
    after_queries = conn.execute("SELECT COUNT(*) AS total FROM local_context_queries").fetchone()["total"]

    assert result["ok"] is True
    assert result["indexed"]["files_found"] >= 1
    assert result["sidecar_query_counter"]["note"] == "legacy_counter_not_real_usage"
    assert after_queries == before_queries


def test_sidecar_index_snapshot_times_out_without_blocking():
    def slow_reader():
        time.sleep(0.2)
        return {"ok": True}

    started = time.time()
    result = health.sidecar_index_snapshot(deadline_ms=1, reader=slow_reader)

    assert result["ok"] is False
    assert result["timed_out"] is True
    assert result["error"] == "local_context_health_timeout"
    assert time.time() - started < 0.15


def test_sidecar_index_snapshot_reports_busy_as_degraded():
    def busy_reader():
        raise sqlite3.OperationalError("database is locked")

    result = health.sidecar_index_snapshot(deadline_ms=300, reader=busy_reader)

    assert result["ok"] is False
    assert result["state"] == "degraded"
    assert result["error"] == "local_context_db_busy"
    assert result["retryable"] is True


def test_local_context_health_separates_indexed_from_real_pre_answer_use():
    def indexed_reader():
        return {
            "ok": True,
            "state": "healthy",
            "indexed": {
                "files_found": 22,
                "files_processed": 7,
                "chunks": 11,
                "entities": 3,
                "jobs_pending": 15,
                "jobs_running": 0,
                "jobs_failed": 0,
                "phase": "initial_indexing",
            },
            "sidecar_query_counter": {"total": 411, "latest_at": 123.0, "note": "legacy_counter_not_real_usage"},
        }

    unused = health.local_context_health(index_reader=indexed_reader)
    assert unused["ok"] is True
    assert unused["separation"]["status"] == "indexed_not_used"
    assert unused["query_health"]["state"] == "no_recent_pre_answer_usage"
    assert unused["query_health"]["legacy_sidecar_query_counter"]["total"] == 411

    usage_events.record_usage_event(
        query="donde esta el plan",
        intent="file_location",
        used_before_response=True,
        should_inject=True,
        result_count=1,
    )

    used = health.local_context_health(index_reader=indexed_reader)
    assert used["ok"] is True
    assert used["separation"]["status"] == "indexed_and_used"
    assert used["query_health"]["state"] == "active"
    assert used["query_health"]["used_before_response_events"] == 1
