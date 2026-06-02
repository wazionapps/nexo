from __future__ import annotations

"""Read-only observatory for the cognitive quality-control phases."""

import json
import os
import sqlite3
import time
from typing import Any

import db
from local_context import usage_events


DEFAULT_WINDOW_SECONDS = 24 * 60 * 60


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _count(conn: sqlite3.Connection, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(conn, table):
        return 0
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    try:
        return int(conn.execute(sql, params).fetchone()[0] or 0)
    except sqlite3.Error:
        return 0


def _status_counts(conn: sqlite3.Connection, table: str) -> dict[str, int]:
    if not _table_exists(conn, table):
        return {}
    try:
        rows = conn.execute(
            f"SELECT COALESCE(status, '') AS status, COUNT(*) AS cnt FROM {table} GROUP BY COALESCE(status, '')"
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["status"] or ""): int(row["cnt"] or 0) for row in rows}


def _local_context_status() -> dict[str, Any]:
    try:
        from local_context import api as local_context_api

        status = local_context_api.status()
        if not isinstance(status, dict):
            return {"ok": False, "error": "invalid_status_payload"}
        return status
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _learning_quality(conn: sqlite3.Connection, limit: int = 100) -> dict[str, Any]:
    if not _table_exists(conn, "learnings"):
        return {"available": False, "total": 0, "active": 0, "status_counts": {}, "quality": {}}
    rows = conn.execute(
        """
        SELECT *
          FROM learnings
         WHERE COALESCE(status, 'active') = 'active'
         ORDER BY updated_at DESC, id DESC
         LIMIT ?
        """,
        (max(1, min(int(limit or 100), 500)),),
    ).fetchall()
    quality_counts = {"strong": 0, "usable": 0, "weak": 0, "fragile": 0}
    scored = 0
    try:
        from tools_learnings import score_learning_quality

        for row in rows:
            score = score_learning_quality(dict(row), conn)
            label = str(score.get("label") or "fragile")
            quality_counts[label] = quality_counts.get(label, 0) + 1
            scored += 1
    except Exception:
        pass
    return {
        "available": True,
        "total": _count(conn, "learnings"),
        "active": _count(conn, "learnings", "COALESCE(status, 'active') = 'active'"),
        "status_counts": _status_counts(conn, "learnings"),
        "quality_sample_size": scored,
        "quality": quality_counts,
    }


def _followups(conn: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(conn, "followups"):
        return {
            "ok": True,
            "skipped": True,
            "reason": "followups_table_unavailable",
            "total": 0,
            "counts": {},
            "executable_now": 0,
            "non_executable": 0,
        }
    try:
        snapshot = db.followup_lifecycle_snapshot(limit=5000)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    counts = dict(snapshot.get("counts") or {})
    executable = int(counts.get("active", 0) or 0)
    non_executable = sum(
        int(counts.get(lane, 0) or 0)
        for lane in ("waiting_user", "waiting_external", "blocked", "parked", "stale_review", "expired")
    )
    return {
        "ok": bool(snapshot.get("ok", True)),
        "total": int(snapshot.get("total") or 0),
        "counts": counts,
        "executable_now": executable,
        "non_executable": non_executable,
    }


def _intraday_memory(conn: sqlite3.Connection, window_seconds: int, now_ts: float) -> dict[str, Any]:
    cutoff = float(now_ts) - max(0, int(window_seconds or DEFAULT_WINDOW_SECONDS))
    try:
        health = db.memory_observation_health()
    except Exception as exc:
        health = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        import memory_observation_processor

        queue = memory_observation_processor.queue_health(now=now_ts)
    except Exception as exc:
        queue = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    facts = 0
    latest_fact = None
    if _table_exists(conn, "hot_context"):
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total, MAX(last_event_at) AS latest
                  FROM hot_context
                 WHERE context_type = 'intraday_fact'
                   AND last_event_at >= ?
                """,
                (cutoff,),
            ).fetchone()
            facts = int(row["total"] or 0)
            latest_fact = row["latest"]
        except sqlite3.Error:
            pass
    schema_ready = not bool(health.get("missing_required"))
    return {
        "ok": (bool(health.get("ok", True)) or not schema_ready) and bool(queue.get("ok", True)),
        "schema_ready": schema_ready,
        "health": health,
        "queue": queue,
        "intraday_facts_window": facts,
        "latest_intraday_fact_at": latest_fact,
        "event_stats": db.memory_event_stats(days=max(1, int(window_seconds / 86400) or 1)),
    }


def build_cognitive_control_observatory(
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    now_ts: float | None = None,
) -> dict[str, Any]:
    """Return read-only metrics for phases 0-4 without creating work."""

    current = float(now_ts if now_ts is not None else time.time())
    conn = db.get_db()
    local_usage = usage_events.summarize_usage(window_seconds=window_seconds, now_ts=current)
    local_status = _local_context_status()
    learning_summary = _learning_quality(conn)
    followup_summary = _followups(conn)
    intraday_summary = _intraday_memory(conn, window_seconds, current)
    local_mode = (
        os.environ.get("NEXO_PRE_ANSWER_LOCAL_CONTEXT_MODE")
        or os.environ.get("NEXO_LOCAL_CONTEXT_PRE_ANSWER_MODE")
        or "inject"
    )
    payload = {
        "ok": True,
        "read_only": True,
        "generated_at": current,
        "window_seconds": max(0, int(window_seconds or DEFAULT_WINDOW_SECONDS)),
        "phase_coverage": {
            "phase_0_observatory": True,
            "phase_1_local_context": True,
            "phase_2_learning_resolver": True,
            "phase_3_followup_lifecycle": True,
            "phase_4_intraday_memory": True,
        },
        "local_context": {
            "pre_answer_mode": str(local_mode).strip().lower(),
            "usage": local_usage,
            "status": local_status,
        },
        "runtime_budgets": local_usage.get("runtime_budget_metrics") or {},
        "learnings": learning_summary,
        "followups": followup_summary,
        "intraday_memory": intraday_summary,
    }
    payload["summary"] = {
        "local_context_events": int(local_usage.get("total_events") or 0),
        "runtime_budget_tiers": len((local_usage.get("runtime_budget_metrics") or {}).get("by_tier") or {}),
        "active_learnings": int(learning_summary.get("active") or 0),
        "active_followups": int((followup_summary.get("counts") or {}).get("active") or 0),
        "intraday_facts": int(intraday_summary.get("intraday_facts_window") or 0),
    }
    return payload


def format_observatory(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


__all__ = ["build_cognitive_control_observatory", "format_observatory"]
