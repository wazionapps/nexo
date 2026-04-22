"""NEXO Brain — canonical lifecycle event handler (v7.4.0).

Companion to nexo-desktop's ConversationLifecycleService. Desktop
persists every conversation/app transition (close / delete / archive /
switch / window-close / app-exit) to an append-only NDJSON queue BEFORE
any UI mutation becomes visible, then calls this handler via the
``nexo_lifecycle_event`` MCP tool. The handler is strictly idempotent:
re-delivery of the same ``event_id`` returns ``already_processed``
without replaying any canonical side effect.

Canonical side effects are intentionally minimal in this first slice:

- ``close`` / ``delete`` / ``archive`` / ``app-exit`` / ``window-close``
  → mark processed. Diary / stop inside the conversation are driven by
  Desktop's graceful-close flow (``conv-close`` IPC → nexo CLI) and
  remain the authority for that per-conversation payload. This table
  is the durable ledger so the next boot can reconcile.
- ``switch`` → mark processed. No canonical side effect beyond the
  audit trail; the ledger still matters for telemetry and guard
  invariants ("operator switched away from a conversation that still
  had uncommitted claims").

Return shape matches the plan (lines 94-100):

- ``processed``          first delivery, side effects (if any) done
- ``already_processed``  duplicate delivery, no re-run
- ``accepted``           persisted, side effect deferred (not used yet)
- ``rejected``           malformed input, no persistence
- ``retryable_error``    transient failure, Desktop should retry

Any row keeps ``delivery_status`` as the latest terminal or retryable
status so ``nexo_lifecycle_status`` / future reconciliation queries
can read it directly.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from db import get_db


VALID_ACTIONS = {
    "close",
    "delete",
    "archive",
    "switch",
    "app-exit",
    "window-close",
}

TERMINAL_STATUSES = {"processed", "already_processed", "rejected"}
_DIARY_TRIGGERING = {"close", "delete", "archive", "app-exit"}


def _normalise_payload(obj: Any) -> str:
    try:
        return json.dumps(obj or {}, ensure_ascii=False, sort_keys=True)
    except Exception:
        return "{}"


def record_lifecycle_event(
    event_id: str,
    action: str,
    conversation_id: str,
    session_id: Optional[str] = None,
    reason: str = "user_action",
    payload_snapshot: Optional[Dict[str, Any]] = None,
    source: str = "desktop",
    schema_version: int = 1,
) -> Dict[str, Any]:
    """Idempotent upsert + process.

    Returns ``{status, event_id, diary_triggered, duplicate}``.
    """
    if not event_id or not str(event_id).strip():
        return {"status": "rejected", "reason": "missing-event-id"}
    if action not in VALID_ACTIONS:
        return {"status": "rejected", "reason": f"unknown-action:{action}"}
    if not conversation_id or not str(conversation_id).strip():
        return {"status": "rejected", "reason": "missing-conversation-id"}

    conn = get_db()
    existing = conn.execute(
        "SELECT delivery_status FROM lifecycle_events WHERE event_id = ?",
        (str(event_id),),
    ).fetchone()

    if existing is not None:
        status = str(existing[0] or "")
        if status in TERMINAL_STATUSES:
            return {
                "status": "already_processed",
                "event_id": event_id,
                "duplicate": True,
                "prior_status": status,
            }
        # Non-terminal row (accepted / retryable_error) — flip to processed
        # now and record the transition.
        conn.execute(
            "UPDATE lifecycle_events SET delivery_status = 'processed', "
            "processed_at = datetime('now'), last_error = NULL "
            "WHERE event_id = ?",
            (str(event_id),),
        )
        conn.commit()
        return {
            "status": "processed",
            "event_id": event_id,
            "diary_triggered": action in _DIARY_TRIGGERING,
            "duplicate": False,
            "reopened": True,
        }

    conn.execute(
        """
        INSERT INTO lifecycle_events (
            event_id, schema_version, source, action, conversation_id,
            session_id, reason, payload_snapshot, delivery_status,
            retry_count, processed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'processed', 0, datetime('now'))
        """,
        (
            str(event_id),
            int(schema_version or 1),
            str(source or "desktop"),
            str(action),
            str(conversation_id),
            str(session_id) if session_id else None,
            str(reason or "user_action"),
            _normalise_payload(payload_snapshot),
        ),
    )
    conn.commit()

    return {
        "status": "processed",
        "event_id": event_id,
        "diary_triggered": action in _DIARY_TRIGGERING,
        "duplicate": False,
    }


def get_lifecycle_event(event_id: str) -> Optional[Dict[str, Any]]:
    if not event_id:
        return None
    row = get_db().execute(
        "SELECT event_id, schema_version, source, action, conversation_id, "
        "session_id, reason, payload_snapshot, delivery_status, retry_count, "
        "created_at, processed_at, last_error "
        "FROM lifecycle_events WHERE event_id = ?",
        (str(event_id),),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row[7] or "{}")
    except Exception:
        payload = {}
    return {
        "event_id": row[0],
        "schema_version": row[1],
        "source": row[2],
        "action": row[3],
        "conversation_id": row[4],
        "session_id": row[5],
        "reason": row[6],
        "payload_snapshot": payload,
        "delivery_status": row[8],
        "retry_count": row[9],
        "created_at": row[10],
        "processed_at": row[11],
        "last_error": row[12],
    }


def list_lifecycle_events_by_status(status: str, limit: int = 100) -> list[Dict[str, Any]]:
    if not status:
        return []
    rows = get_db().execute(
        "SELECT event_id FROM lifecycle_events "
        "WHERE delivery_status = ? ORDER BY created_at ASC LIMIT ?",
        (str(status), int(limit or 100)),
    ).fetchall()
    return [e for e in (get_lifecycle_event(r[0]) for r in rows) if e]
