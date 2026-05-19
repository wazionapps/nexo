from __future__ import annotations

"""Bounded Memory Observations v2 queue processor.

This module keeps the memory-events -> queue -> observations path convergent
without owning schema or MCP wiring. It repairs gaps in existing rows, delegates
the actual observation derivation to the DB layer, and reports queue SLA health.
"""

import hashlib
from typing import Any

import db


DEFAULT_BACKFILL_LIMIT = 100
DEFAULT_PENDING_SLA_SECONDS = 3600
DEFAULT_PROCESS_LIMIT = 100
MAX_BATCH_SIZE = 1000


def _clamp_limit(value: int | None, default: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except Exception:
        parsed = default
    return max(0, min(parsed, MAX_BATCH_SIZE))


def _now(now: float | None = None) -> float:
    return float(now if now is not None else db.now_epoch())


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _tables_available(conn: Any) -> bool:
    return all(
        _table_exists(conn, name)
        for name in ("memory_events", "memory_observation_queue", "memory_observations")
    )


def observation_uid_for_event(event_uid: str) -> str:
    digest = hashlib.sha1(str(event_uid or "").encode("utf-8"), usedforsecurity=False).hexdigest()[:32]
    return f"MO-{digest}"


def _count_unqueued_events(conn: Any) -> int:
    if not _table_exists(conn, "memory_events") or not _table_exists(conn, "memory_observation_queue"):
        return 0
    return int(
        conn.execute(
            """
            SELECT COUNT(*)
              FROM memory_events e
              LEFT JOIN memory_observation_queue q ON q.event_uid = e.event_uid
             WHERE q.event_uid IS NULL
            """
        ).fetchone()[0]
    )


def enqueue_missing_memory_events(*, limit: int = DEFAULT_BACKFILL_LIMIT, now: float | None = None) -> dict:
    """Incrementally queue memory_events that predate queue capture or missed it."""

    conn = db.get_db()
    if not _table_exists(conn, "memory_events") or not _table_exists(conn, "memory_observation_queue"):
        return {"ok": True, "skipped": True, "reason": "memory event queue tables unavailable", "enqueued": 0}

    batch_size = _clamp_limit(limit, DEFAULT_BACKFILL_LIMIT)
    if batch_size <= 0:
        return {"ok": True, "enqueued": 0, "remaining": _count_unqueued_events(conn)}

    rows = conn.execute(
        """
        SELECT e.event_uid, e.created_at
          FROM memory_events e
          LEFT JOIN memory_observation_queue q ON q.event_uid = e.event_uid
         WHERE q.event_uid IS NULL
         ORDER BY e.created_at ASC, e.id ASC
         LIMIT ?
        """,
        (batch_size,),
    ).fetchall()

    enqueued = 0
    stamp = _now(now)
    for row in rows:
        created_at = float(row["created_at"] or stamp)
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO memory_observation_queue (event_uid, status, created_at, updated_at)
            VALUES (?, 'pending', ?, ?)
            """,
            (row["event_uid"], created_at, stamp),
        )
        enqueued += int(cursor.rowcount or 0)
    conn.commit()

    return {"ok": True, "enqueued": enqueued, "seen": len(rows), "remaining": _count_unqueued_events(conn)}


def _processed_rows_missing_observations(conn: Any, *, limit: int | None = None) -> list[dict]:
    if not _tables_available(conn):
        return []
    sql = """
        SELECT q.id, q.event_uid, q.created_at, q.updated_at
          FROM memory_observation_queue q
         WHERE q.status = 'processed'
         ORDER BY q.updated_at ASC, q.id ASC
    """
    params: list[Any] = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(_clamp_limit(limit, MAX_BATCH_SIZE) or MAX_BATCH_SIZE)

    missing: list[dict] = []
    for row in conn.execute(sql, params).fetchall():
        obs_uid = observation_uid_for_event(row["event_uid"])
        exists = conn.execute(
            "SELECT 1 FROM memory_observations WHERE observation_uid = ? LIMIT 1",
            (obs_uid,),
        ).fetchone()
        if exists is None:
            missing.append(dict(row))
            if limit is not None and len(missing) >= _clamp_limit(limit, MAX_BATCH_SIZE):
                break
    return missing


def repair_processed_without_observations(*, limit: int = DEFAULT_BACKFILL_LIMIT, now: float | None = None) -> dict:
    """Requeue rows marked processed when their derived observation is absent."""

    conn = db.get_db()
    if not _tables_available(conn):
        return {"ok": True, "skipped": True, "reason": "memory observation tables unavailable", "requeued": 0}

    rows = _processed_rows_missing_observations(conn, limit=limit)
    stamp = _now(now)
    requeued = 0
    for row in rows:
        cursor = conn.execute(
            """
            UPDATE memory_observation_queue
               SET status = 'pending',
                   last_error = 'requeued: processed queue row had no observation',
                   updated_at = ?,
                   processed_at = NULL
             WHERE id = ?
            """,
            (stamp, row["id"]),
        )
        requeued += int(cursor.rowcount or 0)
    conn.commit()
    return {"ok": True, "requeued": requeued, "seen": len(rows)}


def queue_health(*, pending_sla_seconds: int = DEFAULT_PENDING_SLA_SECONDS, now: float | None = None) -> dict:
    """Return pending/processed queue health with an explicit max-age SLA."""

    conn = db.get_db()
    if not _table_exists(conn, "memory_observation_queue"):
        return {"ok": True, "healthy": True, "skipped": True, "reason": "memory observation queue unavailable"}

    stamp = _now(now)
    sla_seconds = max(1, int(pending_sla_seconds or DEFAULT_PENDING_SLA_SECONDS))
    count_rows = conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM memory_observation_queue GROUP BY status"
    ).fetchall()
    counts = {row["status"]: int(row["cnt"]) for row in count_rows}

    oldest = conn.execute(
        """
        SELECT q.event_uid, q.status, q.created_at, q.updated_at,
               e.event_type, e.source_type, e.source_id
          FROM memory_observation_queue q
          LEFT JOIN memory_events e ON e.event_uid = q.event_uid
         WHERE q.status IN ('pending', 'failed')
         ORDER BY q.created_at ASC, q.id ASC
         LIMIT 1
        """
    ).fetchone()
    oldest_item = dict(oldest) if oldest else None
    max_pending_age = 0.0
    if oldest_item:
        max_pending_age = max(0.0, stamp - float(oldest_item.get("created_at") or stamp))

    stale_cutoff = stamp - sla_seconds
    old_pending_count = int(
        conn.execute(
            """
            SELECT COUNT(*)
              FROM memory_observation_queue
             WHERE status IN ('pending', 'failed')
               AND created_at <= ?
            """,
            (stale_cutoff,),
        ).fetchone()[0]
    )
    unqueued_events = _count_unqueued_events(conn)
    missing_processed = len(_processed_rows_missing_observations(conn))
    warnings: list[dict] = []

    if old_pending_count:
        warnings.append(
            {
                "code": "pending_sla_breached",
                "pending_older_than_sla": old_pending_count,
                "max_pending_age_seconds": max_pending_age,
                "sla_seconds": sla_seconds,
                "oldest": oldest_item,
            }
        )
    if int(counts.get("failed", 0)):
        warnings.append({"code": "queue_failed", "failed": int(counts.get("failed", 0))})
    if unqueued_events:
        warnings.append({"code": "memory_events_not_queued", "unqueued_events": unqueued_events})
    if missing_processed:
        warnings.append({"code": "processed_missing_observation", "processed_missing": missing_processed})

    return {
        "ok": True,
        "healthy": not warnings,
        "counts": counts,
        "pending": int(counts.get("pending", 0)),
        "processed": int(counts.get("processed", 0)),
        "failed": int(counts.get("failed", 0)),
        "oldest_pending": oldest_item,
        "max_pending_age_seconds": max_pending_age,
        "pending_sla_seconds": sla_seconds,
        "pending_sla_ok": old_pending_count == 0,
        "pending_older_than_sla": old_pending_count,
        "unqueued_events": unqueued_events,
        "processed_missing_observations": missing_processed,
        "warnings": warnings,
    }


def process_incremental(
    *,
    process_limit: int = DEFAULT_PROCESS_LIMIT,
    backfill_limit: int = DEFAULT_BACKFILL_LIMIT,
    pending_sla_seconds: int = DEFAULT_PENDING_SLA_SECONDS,
    now: float | None = None,
) -> dict:
    """Run one bounded, idempotent convergence cycle for memory observations."""

    conn = db.get_db()
    if not _tables_available(conn):
        return {
            "ok": True,
            "skipped": True,
            "reason": "memory observation tables unavailable",
            "backfill": {"enqueued": 0},
            "repair": {"requeued": 0},
            "processed": {"processed": 0, "failed": 0},
        }

    backfill = enqueue_missing_memory_events(limit=backfill_limit, now=now)
    repair = repair_processed_without_observations(limit=backfill_limit, now=now)
    processed = db.process_memory_observation_queue(limit=max(1, _clamp_limit(process_limit, DEFAULT_PROCESS_LIMIT)))
    health = queue_health(pending_sla_seconds=pending_sla_seconds, now=now)

    return {
        "ok": bool(processed.get("ok", True)) and bool(backfill.get("ok", True)) and bool(repair.get("ok", True)),
        "healthy": bool(health.get("healthy", True)),
        "backfill": backfill,
        "repair": repair,
        "processed": processed,
        "health": health,
    }
