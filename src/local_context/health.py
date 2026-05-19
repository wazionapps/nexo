from __future__ import annotations

import concurrent.futures
import os
import sqlite3
import time
from typing import Any, Callable

from .db import connect_local_context_db_readonly, local_context_db_path
from .usage_events import DEFAULT_USAGE_WINDOW_SECONDS, summarize_usage, usage_snapshot

DEFAULT_HEALTH_DEADLINE_MS = 500
DEFAULT_DB_TIMEOUT_MS = 120


def _now() -> float:
    return time.time()


def _elapsed_ms(started_at: float) -> int:
    return int(max(0.0, _now() - started_at) * 1000)


def _db_error_code(exc: Exception) -> str:
    text = str(exc).lower()
    if "locked" in text or "busy" in text:
        return "local_context_db_busy"
    if "no such table" in text or "no such column" in text or "schema" in text:
        return "local_context_db_schema_missing"
    if "file is not a database" in text or "malformed" in text:
        return "local_context_db_invalid"
    return "local_context_db_unreadable"


def _deadline_result(reader: Callable[[], dict[str, Any]], *, deadline_ms: int) -> dict[str, Any]:
    budget = max(1, int(deadline_ms or 1))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(reader)
    try:
        return future.result(timeout=budget / 1000.0)
    except concurrent.futures.TimeoutError:
        future.cancel()
        return {
            "ok": False,
            "state": "timeout",
            "error": "local_context_health_timeout",
            "timed_out": True,
            "deadline_ms": budget,
            "retryable": True,
        }
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _count_table(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()
    return int(row["total"] if isinstance(row, sqlite3.Row) else row[0] or 0)


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return row[0]
    return row[0]


def _read_sidecar_snapshot(*, db_timeout_ms: int) -> dict[str, Any]:
    db_path = local_context_db_path()
    if not db_path.exists():
        return {
            "ok": False,
            "state": "missing",
            "error": "local_context_db_missing",
            "retryable": True,
            "db_path": str(db_path),
            "indexed": {
                "files_found": 0,
                "files_processed": 0,
                "chunks": 0,
                "entities": 0,
                "jobs_pending": 0,
                "jobs_running": 0,
                "jobs_failed": 0,
                "phase": "missing",
            },
            "sidecar_query_counter": {"total": 0, "latest_at": 0.0},
        }
    conn = connect_local_context_db_readonly(timeout_ms=max(50, int(db_timeout_ms or DEFAULT_DB_TIMEOUT_MS)))
    try:
        assets = _count_table(conn, "local_assets")
        chunks = _count_table(conn, "local_chunks")
        entities = _count_table(conn, "local_entities")
        query_total = _count_table(conn, "local_context_queries")
        latest_query = 0.0
        if query_total:
            latest_query = float(_scalar(conn, "SELECT MAX(created_at) FROM local_context_queries") or 0.0)

        jobs_pending = jobs_running = jobs_failed = jobs_done = 0
        if _table_exists(conn, "local_index_jobs"):
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS total
                FROM local_index_jobs
                GROUP BY status
                """
            ).fetchall()
            counts = {str(row["status"]): int(row["total"] or 0) for row in rows}
            jobs_pending = counts.get("pending", 0)
            jobs_running = counts.get("running", 0)
            jobs_failed = counts.get("failed", 0)
            jobs_done = counts.get("done", 0)

        active_jobs = jobs_pending + jobs_running + jobs_failed
        if assets <= 0:
            phase = "empty"
        elif active_jobs > 0:
            phase = "initial_indexing"
        else:
            phase = "idle"
        return {
            "ok": True,
            "state": "healthy",
            "db_path": str(db_path),
            "indexed": {
                "files_found": assets,
                "files_processed": jobs_done,
                "chunks": chunks,
                "entities": entities,
                "jobs_pending": jobs_pending,
                "jobs_running": jobs_running,
                "jobs_failed": jobs_failed,
                "phase": phase,
            },
            "sidecar_query_counter": {
                "total": query_total,
                "latest_at": latest_query,
                "note": "legacy_counter_not_real_usage",
            },
        }
    finally:
        conn.close()


def sidecar_index_snapshot(
    *,
    deadline_ms: int = DEFAULT_HEALTH_DEADLINE_MS,
    db_timeout_ms: int = DEFAULT_DB_TIMEOUT_MS,
    reader: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    started_at = _now()

    def read() -> dict[str, Any]:
        if reader is not None:
            return reader()
        return _read_sidecar_snapshot(db_timeout_ms=db_timeout_ms)

    try:
        result = _deadline_result(read, deadline_ms=deadline_ms)
    except FileNotFoundError as exc:
        result = {"ok": False, "state": "missing", "error": "local_context_db_missing", "detail": str(exc), "retryable": True}
    except sqlite3.DatabaseError as exc:
        result = {"ok": False, "state": "degraded", "error": _db_error_code(exc), "detail": str(exc), "retryable": True}
    except Exception as exc:
        result = {"ok": False, "state": "degraded", "error": type(exc).__name__, "detail": str(exc), "retryable": True}
    result.setdefault("deadline_ms", int(deadline_ms or 0))
    result["elapsed_ms"] = _elapsed_ms(started_at)
    return result


def telemetry_health(
    *,
    window_seconds: int = DEFAULT_USAGE_WINDOW_SECONDS,
    usage_db_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    summary = summarize_usage(window_seconds=window_seconds, db_path=usage_db_path)
    if not summary.get("ok"):
        return {
            "ok": False,
            "state": "degraded",
            "error": summary.get("error") or "usage_store_unavailable",
            "summary": summary,
        }
    return {
        "ok": True,
        "state": "healthy",
        "summary": summary,
    }


def local_context_health(
    *,
    deadline_ms: int = DEFAULT_HEALTH_DEADLINE_MS,
    db_timeout_ms: int = DEFAULT_DB_TIMEOUT_MS,
    window_seconds: int = DEFAULT_USAGE_WINDOW_SECONDS,
    usage_db_path: str | os.PathLike[str] | None = None,
    index_reader: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    indexer = sidecar_index_snapshot(
        deadline_ms=deadline_ms,
        db_timeout_ms=db_timeout_ms,
        reader=index_reader,
    )
    telemetry = telemetry_health(window_seconds=window_seconds, usage_db_path=usage_db_path)
    indexed = indexer.get("indexed") or {}
    separation = usage_snapshot(
        indexed_files=int(indexed.get("files_found") or 0) if indexer.get("ok") else None,
        index_phase=str(indexed.get("phase") or indexer.get("state") or ""),
        window_seconds=window_seconds,
        db_path=usage_db_path,
    )
    query_state = "active" if separation["used_before_response"]["events"] > 0 else "no_recent_pre_answer_usage"
    if not indexer.get("ok"):
        query_state = "unknown_index_unhealthy"
    elif separation["indexed"]["files_found"] <= 0:
        query_state = "not_indexed"
    ok = bool(indexer.get("ok")) and bool(telemetry.get("ok"))
    return {
        "ok": ok,
        "state": "healthy" if ok else "degraded",
        "indexer_health": indexer,
        "query_health": {
            "ok": bool(telemetry.get("ok")),
            "state": query_state,
            "used_before_response_events": separation["used_before_response"]["events"],
            "latest_used_before_response_at": separation["used_before_response"]["latest_at"],
            "legacy_sidecar_query_counter": (indexer.get("sidecar_query_counter") or {}),
        },
        "telemetry_health": telemetry,
        "separation": separation,
    }
