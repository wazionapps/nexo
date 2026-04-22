"""Lifecycle Events MCP plugin — nexo_lifecycle_event tool (v7.4.0).

Exposes the canonical handler as an MCP tool so nexo-desktop's
ConversationLifecycleService can relay close/delete/archive/app-exit/
window-close intents to Brain and get a formal acknowledgement.

See src/lifecycle_events.py for the handler contract and
guardian-claude-desktop-plan.md for the overall architecture.
"""
from __future__ import annotations

import json
from typing import Any

import lifecycle_events


def handle_nexo_lifecycle_event(
    event_id: str,
    action: str,
    conversation_id: str,
    session_id: str = "",
    reason: str = "user_action",
    payload_snapshot: str = "",
    source: str = "desktop",
    schema_version: int = 1,
) -> str:
    """Record a durable lifecycle event and return the canonical ack.

    Desktop (or any future client) MUST persist the event locally first
    (NDJSON queue) before calling this tool. The handler is idempotent:
    re-delivery of the same event_id returns ``already_processed``.

    Args:
        event_id: UUID minted by the client. Primary idempotency key.
        action: One of ``close`` / ``delete`` / ``archive`` / ``switch``
            / ``app-exit`` / ``window-close``.
        conversation_id: Client-side conversation identifier.
        session_id: Claude session id that backs the conversation, when
            known. Optional.
        reason: Free-form origin tag. Default ``user_action``.
        payload_snapshot: JSON-encoded snapshot of the conversation at
            the moment of the click (title, last_message_at, is_active,
            etc). Accepts an empty string if nothing was snapped.
        source: Client identifier. Default ``desktop``.
        schema_version: Event schema version the client emitted. Default 1.

    Returns:
        JSON string ``{status, event_id, ...}``. ``status`` is one of
        ``processed`` / ``already_processed`` / ``accepted`` /
        ``rejected`` / ``retryable_error``.
    """
    payload_obj: Any = {}
    if payload_snapshot:
        try:
            parsed = json.loads(payload_snapshot)
            if isinstance(parsed, dict):
                payload_obj = parsed
        except Exception:
            payload_obj = {"_raw": str(payload_snapshot)[:4096]}

    try:
        result = lifecycle_events.record_lifecycle_event(
            event_id=event_id,
            action=action,
            conversation_id=conversation_id,
            session_id=session_id or None,
            reason=reason or "user_action",
            payload_snapshot=payload_obj,
            source=source or "desktop",
            schema_version=int(schema_version or 1),
        )
    except Exception as exc:
        return json.dumps({
            "status": "retryable_error",
            "reason": f"{type(exc).__name__}: {exc}",
            "handler_threw": True,
        }, ensure_ascii=False)

    return json.dumps(result, ensure_ascii=False)


def handle_nexo_lifecycle_status(event_id: str) -> str:
    """Read the current delivery_status of a lifecycle event.

    Primarily used by reconciliation at Desktop boot: for each
    still-pending or retryable event in the local NDJSON queue, ask
    Brain whether it already processed it (Desktop crashed between the
    append and the ack) or whether we need to re-submit.
    """
    if not event_id:
        return json.dumps({"status": "rejected", "reason": "missing-event-id"})
    try:
        row = lifecycle_events.get_lifecycle_event(event_id)
    except Exception as exc:
        return json.dumps({"status": "retryable_error", "reason": f"{type(exc).__name__}: {exc}"})
    if row is None:
        return json.dumps({"status": "not_found", "event_id": event_id})
    return json.dumps(row, ensure_ascii=False)


TOOLS = [
    (
        handle_nexo_lifecycle_event,
        "nexo_lifecycle_event",
        "Record a durable lifecycle event (close/delete/archive/switch/app-exit/window-close) and return a canonical ack.",
    ),
    (
        handle_nexo_lifecycle_status,
        "nexo_lifecycle_status",
        "Read the current delivery_status of a lifecycle event. Used by Desktop boot reconciliation.",
    ),
]
