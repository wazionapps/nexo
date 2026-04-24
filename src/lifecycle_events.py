"""NEXO Brain — canonical lifecycle event handler (v7.5).

v7.4.x shipped this tool as a pure ledger + reconciliation surface:
Desktop persisted every conversation lifecycle transition locally,
called ``nexo_lifecycle_event`` for book-keeping, and ran its own
hardcoded ``diary + stop`` prompts against the live Claude process
during ``closeConversationGraceful``.

v7.5 promotes this handler to the **canonical authority** for
session-end. For every ``close`` / ``delete`` / ``archive`` /
``app-exit`` event with a live ``session_id``, Brain now generates a
deterministic **canonical plan** (``canonical_plan_id``, versioned
action list) and hands it back in the same MCP call. Desktop executes
the plan inline (Desktop is still the only process that can reach the
Claude proc's stdin) and then calls
``nexo_lifecycle_complete_canonical`` with per-action results. Brain
records ``canonical_done_at`` only on that second call — no polling.

Idempotency is real, not cosmetic:

1. ``canonical_plan_id`` is deterministic: ``sha256(event_id + version)``.
   A retry of the same event returns the same plan id, which Desktop
   can use to skip actions it already completed locally.
2. Before regenerating a plan for a previously dispatched event, Brain
   checks whether the session already wrote a ``session_diary`` row
   after the original ``canonical_dispatched_at``. If it did → the
   answer is ``already_processed``; no re-dispatch, no duplicate diary.

Status values:

- ``processed``           first delivery, no canonical plan applicable
                          (e.g. switch / window-close / missing
                          session_id).
- ``canonical_pending``   plan generated and returned. Desktop is
                          expected to execute + confirm.
- ``canonical_dispatched`` alias for ``canonical_pending`` on a row
                          that already has ``canonical_dispatched_at``
                          set (re-delivery case).
- ``canonical_done``      Desktop confirmed via complete_canonical.
- ``already_processed``   idempotent duplicate, no re-run.
- ``accepted``            persisted, no canonical side effect required.
- ``rejected``            malformed input.
- ``retryable_error``     a canonical action failed (inject timeout,
                          stdin closed, etc). Reconciler can retry
                          with the same plan_id.

Actions that carry a canonical plan: ``close``, ``delete``, ``archive``,
``app-exit``. ``switch`` and ``window-close`` still return
``accepted`` (no live-session work to do).
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from db import get_db
from db._core import SESSION_STALE_SECONDS
import lifecycle_prompts


VALID_ACTIONS = {
    "close",
    "delete",
    "archive",
    "switch",
    "app-exit",
    "window-close",
}

# Terminal for the user of the ledger (no further action expected).
TERMINAL_STATUSES = {
    "processed",
    "canonical_done",
    "already_processed",
    "rejected",
}
_DIARY_TRIGGERING = lifecycle_prompts.DIARY_TRIGGERING_ACTIONS
SESSION_NOT_LINKED_REASON = "session-not-linked-to-nexo"


def _normalise_payload(obj: Any) -> str:
    try:
        return json.dumps(obj or {}, ensure_ascii=False, sort_keys=True)
    except Exception:
        return "{}"


def _session_diary_session_ids(conn, session_id: str) -> List[str]:
    """Return all session ids that can contain diary evidence for a lifecycle session.

    Desktop passes Claude's conversation/session UUID as ``lifecycle_events.session_id``.
    ``nexo_session_diary_write`` stores rows under the active NEXO SID (``nexo-...``).
    The alias table links both values, so canonical diary confirmation must check the
    direct id and its NEXO aliases.
    """
    raw = str(session_id or "").strip()
    if not raw:
        return []
    ids: List[str] = [raw]
    try:
        rows = conn.execute(
            "SELECT sid FROM session_claude_aliases "
            "WHERE claude_session_id = ? ORDER BY last_seen DESC",
            (raw,),
        ).fetchall()
        ids.extend(str(row[0]) for row in rows if row and row[0])
    except Exception:
        pass
    try:
        rows = conn.execute(
            "SELECT sid FROM sessions "
            "WHERE claude_session_id = ? OR external_session_id = ? "
            "ORDER BY last_update_epoch DESC",
            (raw, raw),
        ).fetchall()
        ids.extend(str(row[0]) for row in rows if row and row[0])
    except Exception:
        pass

    deduped: List[str] = []
    seen = set()
    for sid in ids:
        if sid and sid not in seen:
            seen.add(sid)
            deduped.append(sid)
    return deduped


def _registered_session_ids(conn, session_id: str, candidates: Optional[List[str]] = None) -> List[str]:
    """Return real active-session table ids linked to a lifecycle session id."""
    raw = str(session_id or "").strip()
    ids = [str(s or "").strip() for s in list(candidates or []) if str(s or "").strip()]
    if raw and raw not in ids:
        ids.append(raw)
    rows = []
    try:
        if ids:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                "SELECT sid FROM sessions "
                f"WHERE sid IN ({placeholders}) OR external_session_id = ? OR claude_session_id = ? "
                "ORDER BY last_update_epoch DESC",
                (*ids, raw, raw),
            ).fetchall()
        elif raw:
            rows = conn.execute(
                "SELECT sid FROM sessions "
                "WHERE external_session_id = ? OR claude_session_id = ? OR sid = ? "
                "ORDER BY last_update_epoch DESC",
                (raw, raw, raw),
            ).fetchall()
    except Exception:
        rows = []

    deduped: List[str] = []
    seen = set()
    for row in rows:
        sid = str(row[0] or "").strip() if row else ""
        if sid and sid not in seen:
            seen.add(sid)
            deduped.append(sid)
    return deduped


def _linked_external_session_ids(conn, session_id: str) -> List[str]:
    """Return external Claude/Desktop ids linked to a NEXO SID or raw session id."""
    raw = str(session_id or "").strip()
    if not raw:
        return []
    ids: List[str] = []
    if not raw.startswith("nexo-"):
        ids.append(raw)
    try:
        rows = conn.execute(
            "SELECT claude_session_id FROM session_claude_aliases WHERE sid = ? "
            "ORDER BY last_seen DESC",
            (raw,),
        ).fetchall()
        ids.extend(str(row[0] or "").strip() for row in rows if row and row[0])
    except Exception:
        pass
    try:
        rows = conn.execute(
            "SELECT external_session_id, claude_session_id FROM sessions WHERE sid = ?",
            (raw,),
        ).fetchall()
        for row in rows:
            ids.extend(str(value or "").strip() for value in row if value)
    except Exception:
        pass

    deduped: List[str] = []
    seen = set()
    for external_id in ids:
        if external_id and external_id not in seen:
            seen.add(external_id)
            deduped.append(external_id)
    return deduped


def _registered_stop_session_ids(conn, session_id: str) -> List[str]:
    """Resolve a lifecycle stop target to real registered NEXO SIDs."""
    raw = str(session_id or "").strip()
    if not raw:
        return []
    candidates = _session_diary_session_ids(conn, raw)
    external_ids = _linked_external_session_ids(conn, raw)
    for external_id in external_ids:
        candidates.extend(_session_diary_session_ids(conn, external_id))

    registered: List[str] = []
    registered.extend(_registered_session_ids(conn, raw, candidates))
    for external_id in external_ids:
        registered.extend(_registered_session_ids(conn, external_id, candidates))

    deduped: List[str] = []
    seen = set()
    for sid in registered:
        if sid and sid not in seen:
            seen.add(sid)
            deduped.append(sid)
    if deduped:
        return deduped
    return [raw] if raw.startswith("nexo-") else []


def registered_stop_session_ids(session_id: str) -> List[str]:
    """Public wrapper used by plugin handlers before calling nexo_stop."""
    return _registered_stop_session_ids(get_db(), session_id)


def _session_is_linked_to_nexo(conn, session_id: str) -> bool:
    """True when the external Claude/Desktop session is linked to a NEXO SID."""
    raw = str(session_id or "").strip()
    if not raw:
        return False
    if raw.startswith("nexo-"):
        return True
    try:
        row = conn.execute(
            "SELECT 1 FROM session_claude_aliases WHERE claude_session_id = ? LIMIT 1",
            (raw,),
        ).fetchone()
        if row is not None:
            return True
    except Exception:
        pass
    try:
        row = conn.execute(
            "SELECT 1 FROM sessions "
            "WHERE external_session_id = ? OR claude_session_id = ? "
            "LIMIT 1",
            (raw, raw),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _max_session_diary_id(conn, session_id: str) -> int:
    session_ids = _session_diary_session_ids(conn, session_id)
    if not session_ids:
        return 0
    placeholders = ",".join("?" for _ in session_ids)
    try:
        row = conn.execute(
            f"SELECT COALESCE(MAX(id), 0) FROM session_diary WHERE session_id IN ({placeholders})",
            tuple(session_ids),
        ).fetchone()
    except Exception:
        return 0
    try:
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _diary_checkpoint_from_actions_json(actions_json: Optional[str]) -> int:
    if not actions_json:
        return 0
    try:
        actions = json.loads(actions_json)
    except Exception:
        return 0
    if not isinstance(actions, list):
        return 0
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = action.get("type") or action.get("kind")
        if action_type != "wait_for_diary_write":
            continue
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        raw = payload.get("after_session_diary_id", action.get("after_session_diary_id", 0))
        try:
            return int(raw or 0)
        except Exception:
            return 0
    return 0


def _attach_diary_checkpoint(plan: Dict[str, Any], checkpoint_id: int) -> Dict[str, Any]:
    """Store the session_diary high-water mark inside the persisted plan.

    ``created_at`` only has second precision in old installs. The
    checkpoint makes diary confirmation robust even when dispatch and
    diary write happen in the same second.
    """
    actions = []
    for action in list((plan or {}).get("canonical_actions") or []):
        item = dict(action or {})
        action_type = item.get("type") or item.get("kind")
        if action_type == "wait_for_diary_write":
            payload = dict(item.get("payload") or {})
            payload["after_session_diary_id"] = int(checkpoint_id or 0)
            item["payload"] = payload
            item["after_session_diary_id"] = int(checkpoint_id or 0)
        actions.append(item)
    updated = dict(plan or {})
    updated["canonical_actions"] = actions
    return updated


def _session_diary_evidence(
    conn,
    session_id: str,
    dispatched_at: Optional[str],
    actions_json: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the concrete session_diary row that satisfies a plan."""
    if not session_id or not dispatched_at:
        return None
    checkpoint_id = _diary_checkpoint_from_actions_json(actions_json)
    session_ids = _session_diary_session_ids(conn, session_id)
    if not session_ids:
        return None
    placeholders = ",".join("?" for _ in session_ids)
    try:
        if checkpoint_id > 0:
            row = conn.execute(
                "SELECT id, created_at, session_id FROM session_diary "
                f"WHERE session_id IN ({placeholders}) AND id > ? ORDER BY id ASC LIMIT 1",
                (*session_ids, int(checkpoint_id)),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, created_at, session_id FROM session_diary "
                f"WHERE session_id IN ({placeholders}) AND created_at >= ? "
                "ORDER BY created_at ASC, id ASC LIMIT 1",
                (*session_ids, str(dispatched_at)),
            ).fetchone()
    except Exception:
        # Missing table on a minimal test harness — treat as "no diary".
        return None
    if row is None:
        return None
    return {"session_diary_id": row[0], "created_at": row[1], "diary_session_id": row[2]}


def _session_diary_since(conn, session_id: str, dispatched_at: Optional[str], actions_json: Optional[str] = None) -> bool:
    """True if session_diary has evidence satisfying the canonical plan."""
    return _session_diary_evidence(conn, session_id, dispatched_at, actions_json) is not None


def _preferred_diary_session_id(conn, session_id: str) -> str:
    """Return the best session id to store fallback diary evidence under."""
    raw = str(session_id or "").strip()
    candidates = _session_diary_session_ids(conn, raw)
    for sid in _registered_session_ids(conn, raw, candidates):
        if str(sid or "").startswith("nexo-"):
            return str(sid)
    for sid in candidates:
        if str(sid or "").startswith("nexo-"):
            return str(sid)
    return candidates[0] if candidates else raw


def _payload_lines(payload: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    if not isinstance(payload, dict):
        return lines
    for raw in payload.get("transcript_tail") or []:
        text = str(raw or "").strip()
        if text:
            lines.append(text)
    if not lines:
        messages = payload.get("messages")
        if isinstance(messages, list):
            for item in messages[-12:]:
                if isinstance(item, dict):
                    role = str(item.get("role") or item.get("type") or item.get("sender") or "message").strip()
                    text = str(item.get("content") or item.get("text") or "").strip()
                else:
                    role = "message"
                    text = str(item or "").strip()
                if text:
                    lines.append(f"{role}: {text}")
    if not lines:
        user = str(payload.get("last_user_message") or payload.get("latest_user_text") or "").strip()
        assistant = str(payload.get("last_assistant_message") or payload.get("latest_assistant_text") or "").strip()
        if user:
            lines.append(f"user: {user}")
        if assistant:
            lines.append(f"assistant: {assistant}")
    return lines[-12:]


def _parse_payload_json(raw: Any) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _payload_has_context(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if _payload_lines(payload):
        return True
    for key in ("current_goal", "last_user_message", "latest_user_text", "last_assistant_message", "latest_assistant_text"):
        if str(payload.get(key) or "").strip():
            return True
    return False


def _continuity_payloads_for_event(
    conn,
    conversation_id: str,
    session_id: str,
    limit: int = 24,
) -> List[Dict[str, Any]]:
    """Return recent continuity payloads for richer emergency diaries."""
    conv = str(conversation_id or "").strip()
    sid = str(session_id or "").strip()
    if not conv and not sid:
        return []

    where_parts: List[str] = []
    params: List[Any] = []
    if conv:
        where_parts.append("conversation_id = ?")
        params.append(conv)
    if sid:
        where_parts.append("session_id = ?")
        params.append(sid)
    where = " OR ".join(where_parts)

    try:
        rows = conn.execute(
            "SELECT event_type, payload_json FROM continuity_snapshots "
            f"WHERE ({where}) "
            "ORDER BY id DESC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
    except Exception:
        return []

    payloads: List[Dict[str, Any]] = []
    for row in reversed(rows):
        event_type = str(row[0] or "").strip()
        if event_type and event_type not in {"turn_end", "app_exit", "desktop_snapshot", "close", "archive"}:
            continue
        payload = _parse_payload_json(row[1] if len(row) > 1 else "")
        if payload:
            payloads.append(payload)
    return payloads


def _dedupe_lines(lines: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for raw in lines:
        line = str(raw or "").strip()
        if not line or line in seen:
            continue
        seen.add(line)
        result.append(line)
    return result


def _enrich_payload_from_continuity(
    conn,
    payload: Dict[str, Any],
    conversation_id: str,
    session_id: str,
) -> Dict[str, Any]:
    """Fill sparse lifecycle payloads from durable continuity snapshots."""
    enriched = dict(payload or {})
    continuity_payloads = _continuity_payloads_for_event(conn, conversation_id, session_id)
    if not continuity_payloads:
        return enriched

    latest = continuity_payloads[-1]
    for key in (
        "title",
        "current_goal",
        "last_user_message",
        "latest_user_text",
        "last_assistant_message",
        "latest_assistant_text",
    ):
        if not str(enriched.get(key) or "").strip() and str(latest.get(key) or "").strip():
            enriched[key] = latest[key]

    continuity_lines: List[str] = []
    for item in continuity_payloads:
        continuity_lines.extend(_payload_lines(item))
    continuity_lines = _dedupe_lines(continuity_lines)
    current_lines = _payload_lines(enriched)
    if len(continuity_lines) > len(current_lines) or not _payload_has_context(enriched):
        enriched["transcript_tail"] = continuity_lines[-12:]
    return enriched


def write_fallback_diary_for_lifecycle_event(
    event_id: str,
    reason: str = "",
    source: str = "desktop-lifecycle-fallback",
) -> Dict[str, Any]:
    """Write minimum durable diary evidence when live-agent injection fails.

    This is the safety net for Desktop close/archive/app-exit. The preferred
    path remains an agent-authored ``nexo_session_diary_write``. If the agent is
    busy or stdin never produces a response, Desktop can call this command so
    session continuity still has a concrete ``session_diary`` row instead of
    silently losing the last context.
    """
    if not event_id:
        return {"status": "rejected", "reason": "missing-event-id"}

    conn = get_db()
    row = conn.execute(
        "SELECT action, conversation_id, session_id, reason, payload_snapshot, "
        "canonical_dispatched_at, canonical_actions_json "
        "FROM lifecycle_events WHERE event_id = ?",
        (str(event_id),),
    ).fetchone()
    if row is None:
        return {"status": "rejected", "reason": "unknown-event-id", "event_id": event_id}

    action = str(row[0] or "")
    conversation_id = str(row[1] or "")
    session_id = str(row[2] or "")
    lifecycle_reason = str(row[3] or "")
    dispatched_at = row[5]
    actions_json = row[6]
    if action not in _DIARY_TRIGGERING:
        return {"status": "processed", "event_id": event_id, "diary_required": False}
    if not session_id:
        return {"status": "rejected", "reason": "missing-session-id", "event_id": event_id}

    existing = _session_diary_evidence(conn, session_id, dispatched_at, actions_json)
    if existing is not None:
        return {
            "status": "ok",
            "event_id": event_id,
            "fallback_written": False,
            "diary_confirmed": True,
            **existing,
        }

    try:
        payload = json.loads(row[4] or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    payload = _enrich_payload_from_continuity(conn, payload, conversation_id, session_id)

    title = str(payload.get("title") or conversation_id or event_id).strip()
    transcript_lines = _payload_lines(payload)
    technical_reason = str(reason or lifecycle_reason or "fallback-diary").strip()
    diary_session_id = _preferred_diary_session_id(conn, session_id)
    summary = (
        "Diario automatico de emergencia generado por NEXO Desktop al cerrar "
        f"'{title}'. No se confirmo un diario escrito por el agente vivo, asi "
        "que se preserva el snapshot disponible para continuidad."
    )
    decisions = (
        f"Accion de ciclo de vida: {action}. Evento: {event_id}. "
        f"Motivo tecnico: {technical_reason}."
    )
    pending = str(payload.get("current_goal") or payload.get("last_user_message") or "").strip()
    if not pending:
        pending = "Revisar la conversacion al reabrir y continuar desde el snapshot preservado."
    context_next_parts = [
        f"conversation_id={conversation_id}",
        f"session_id={session_id}",
    ]
    if transcript_lines:
        context_next_parts.append("Transcript tail:\n" + "\n".join(transcript_lines))
    context_next = "\n".join(context_next_parts)[:8000]

    from db import write_session_diary

    diary = write_session_diary(
        diary_session_id,
        decisions=decisions,
        summary=summary,
        discarded="",
        pending=pending,
        context_next=context_next,
        mental_state="Fallback automatico: el agente vivo no confirmo el cierre dentro del timeout.",
        domain="nexo-desktop",
        user_signals="Cierre/archivo de conversacion; preservar informacion antes de salir.",
        self_critique="El cierre no debe depender exclusivamente de que el agente responda a tiempo.",
        source=source or "desktop-lifecycle-fallback",
    )
    return {
        "status": "ok",
        "event_id": event_id,
        "fallback_written": True,
        "diary_confirmed": True,
        "session_diary_id": diary.get("id"),
        "diary_session_id": diary_session_id,
        "source": source or "desktop-lifecycle-fallback",
    }


def _session_stop_state(conn, session_id: str) -> Dict[str, Any]:
    """Return whether the lifecycle session can be verified as fully stopped."""
    raw = str(session_id or "").strip()
    if not raw:
        return {
            "verifiable": False,
            "session_registered": False,
            "stop_confirmed": False,
            "active_session_ids": [],
        }

    candidate_ids: List[str] = []
    verifiable = False

    if raw.startswith("nexo-"):
        candidate_ids.append(raw)
        verifiable = True

    try:
        rows = conn.execute(
            "SELECT sid FROM session_claude_aliases "
            "WHERE claude_session_id = ? ORDER BY last_seen DESC",
            (raw,),
        ).fetchall()
        alias_ids = [str(row[0]) for row in rows if row and row[0]]
        if alias_ids:
            candidate_ids.extend(alias_ids)
            verifiable = True
    except Exception:
        pass

    try:
        rows = conn.execute(
            "SELECT sid FROM sessions "
            "WHERE external_session_id = ? OR claude_session_id = ? OR sid = ? "
            "ORDER BY last_update_epoch DESC",
            (raw, raw, raw),
        ).fetchall()
        live_ids = [str(row[0]) for row in rows if row and row[0]]
        if live_ids:
            candidate_ids.extend(live_ids)
            verifiable = True
    except Exception:
        pass

    deduped_candidates: List[str] = []
    seen = set()
    for sid in candidate_ids:
        if sid and sid not in seen:
            seen.add(sid)
            deduped_candidates.append(sid)

    if not verifiable:
        return {
            "verifiable": False,
            "session_registered": False,
            "stop_confirmed": False,
            "active_session_ids": [],
        }

    cutoff = time.time() - float(SESSION_STALE_SECONDS)
    active_ids: List[str] = []
    try:
        if deduped_candidates:
            placeholders = ",".join("?" for _ in deduped_candidates)
            rows = conn.execute(
                "SELECT sid FROM sessions "
                f"WHERE last_update_epoch > ? AND (sid IN ({placeholders}) OR external_session_id = ? OR claude_session_id = ?) "
                "ORDER BY last_update_epoch DESC",
                (cutoff, *deduped_candidates, raw, raw),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT sid FROM sessions "
                "WHERE last_update_epoch > ? AND (external_session_id = ? OR claude_session_id = ?) "
                "ORDER BY last_update_epoch DESC",
                (cutoff, raw, raw),
            ).fetchall()
        active_ids = [str(row[0]) for row in rows if row and row[0]]
    except Exception:
        active_ids = []

    deduped_active: List[str] = []
    seen.clear()
    for sid in active_ids:
        if sid and sid not in seen:
            seen.add(sid)
            deduped_active.append(sid)

    return {
        "verifiable": True,
        "session_registered": True,
        "stop_confirmed": len(deduped_active) == 0,
        "active_session_ids": deduped_active,
    }


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
    """Idempotent upsert + canonical plan generation (v7.5).

    Returns ``{status, event_id, ...}`` where ``status`` is one of:
    ``rejected`` | ``already_processed`` | ``processed`` |
    ``canonical_pending`` | ``accepted``. When the answer is
    ``canonical_pending``, the response also carries
    ``canonical_plan_id``, ``canonical_plan_version`` and
    ``canonical_actions[]`` — Desktop must execute those actions and
    confirm via ``record_complete_canonical``.
    """
    if not event_id or not str(event_id).strip():
        return {"status": "rejected", "reason": "missing-event-id"}
    if action not in VALID_ACTIONS:
        return {"status": "rejected", "reason": f"unknown-action:{action}"}
    if not conversation_id or not str(conversation_id).strip():
        return {"status": "rejected", "reason": "missing-conversation-id"}

    conn = get_db()
    existing = conn.execute(
        "SELECT delivery_status, canonical_plan_id, canonical_plan_version, "
        "canonical_actions_json, canonical_dispatched_at, canonical_done_at "
        "FROM lifecycle_events WHERE event_id = ?",
        (str(event_id),),
    ).fetchone()

    plan = lifecycle_prompts.build_canonical_plan(
        event_id=str(event_id),
        action=str(action),
        conversation_id=str(conversation_id),
        session_id=str(session_id) if session_id else None,
        payload_snapshot=payload_snapshot or {},
    )
    if plan is not None and session_id:
        plan = _attach_diary_checkpoint(plan, _max_session_diary_id(conn, str(session_id)))

    if existing is not None:
        status = str(existing[0] or "")
        prior_plan_id = existing[1]
        prior_actions_json = existing[3]
        prior_dispatched_at = existing[5]  # column 5 is canonical_done_at — reuse?
        # column indices (0-5): delivery_status, canonical_plan_id,
        # canonical_plan_version, canonical_actions_json,
        # canonical_dispatched_at, canonical_done_at.
        prior_dispatched_at = existing[4]
        prior_done_at = existing[5]

        # Case A: terminal status already recorded — hard idempotency.
        if status in TERMINAL_STATUSES:
            return {
                "status": "already_processed",
                "event_id": event_id,
                "duplicate": True,
                "prior_status": status,
            }

        # Case B: canonical was dispatched but never confirmed. Only
        # short-circuit if the session already wrote the diary AND no
        # linked NEXO session remains active.
        if prior_plan_id and prior_dispatched_at and not prior_done_at:
            stop_state = _session_stop_state(conn, str(session_id)) if session_id else None
            stop_confirmed = bool(stop_state and stop_state.get("verifiable") and stop_state.get("stop_confirmed"))
            if session_id and _session_diary_since(
                conn,
                str(session_id),
                str(prior_dispatched_at),
                str(prior_actions_json or ""),
            ) and stop_confirmed:
                conn.execute(
                    "UPDATE lifecycle_events "
                    "SET delivery_status = 'already_processed', "
                    "    canonical_done_at = datetime('now'), "
                    "    last_error = NULL "
                    "WHERE event_id = ?",
                    (str(event_id),),
                )
                conn.commit()
                return {
                    "status": "already_processed",
                    "event_id": event_id,
                    "duplicate": True,
                    "prior_status": status,
                    "reason": "session_diary-and-stop-already-written",
                }
            # Re-hand the exact same plan so Desktop can resume / finish
            # any actions it didn't complete before the crash.
            try:
                actions = json.loads(prior_actions_json) if prior_actions_json else []
            except Exception:
                actions = []
            return {
                "status": "canonical_pending",
                "event_id": event_id,
                "canonical_plan_id": prior_plan_id,
                "canonical_plan_version": int(existing[2] or lifecycle_prompts.PLAN_VERSION),
                "canonical_actions": actions,
                "resumed_from_dispatch": True,
            }

        # Case C: non-terminal, no canonical plan yet — flip to processed
        # (legacy ledger semantics) OR upgrade to canonical_pending if a
        # plan applies.
        if plan is not None:
            conn.execute(
                "UPDATE lifecycle_events "
                "SET delivery_status = 'canonical_pending', "
                "    canonical_plan_id = ?, "
                "    canonical_plan_version = ?, "
                "    canonical_actions_json = ?, "
                "    canonical_dispatched_at = datetime('now'), "
                "    last_error = NULL "
                "WHERE event_id = ?",
                (
                    plan["canonical_plan_id"],
                    int(plan["canonical_plan_version"]),
                    json.dumps(plan["canonical_actions"], ensure_ascii=False),
                    str(event_id),
                ),
            )
            conn.commit()
            return {
                "status": "canonical_pending",
                "event_id": event_id,
                "canonical_plan_id": plan["canonical_plan_id"],
                "canonical_plan_version": plan["canonical_plan_version"],
                "canonical_actions": plan["canonical_actions"],
                "reopened": True,
            }
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

    # Brand new event.
    if plan is not None:
        conn.execute(
            """
            INSERT INTO lifecycle_events (
                event_id, schema_version, source, action, conversation_id,
                session_id, reason, payload_snapshot, delivery_status,
                retry_count, canonical_plan_id, canonical_plan_version,
                canonical_actions_json, canonical_dispatched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'canonical_pending', 0,
                      ?, ?, ?, datetime('now'))
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
                plan["canonical_plan_id"],
                int(plan["canonical_plan_version"]),
                json.dumps(plan["canonical_actions"], ensure_ascii=False),
            ),
        )
        conn.commit()
        return {
            "status": "canonical_pending",
            "event_id": event_id,
            "canonical_plan_id": plan["canonical_plan_id"],
            "canonical_plan_version": plan["canonical_plan_version"],
            "canonical_actions": plan["canonical_actions"],
            "duplicate": False,
        }

    # No plan: ledger-only record (switch/window-close or missing
    # session_id on a diary-triggering action). Mark processed
    # immediately so callers get the v7.4.x contract back.
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


def record_complete_canonical(
    event_id: str,
    canonical_plan_id: str,
    results: Optional[list] = None,
) -> Dict[str, Any]:
    """Close the 2-call contract: Desktop confirms it executed the plan.

    Inputs:
    - ``event_id``: the original event id.
    - ``canonical_plan_id``: must match the one Brain handed out. A
      mismatch means Desktop is confirming a stale plan — we ignore
      it and answer ``rejected``.
    - ``results``: list of ``{action_id, status, ...}``. If any
      ``status != 'ok'`` we flip the row to ``retryable_error`` and
      keep ``canonical_dispatched_at`` intact so reconciliation can
      re-ask.

    Returns the effective row status after the call.
    """
    if not event_id:
        return {"status": "rejected", "reason": "missing-event-id"}
    if not canonical_plan_id:
        return {"status": "rejected", "reason": "missing-canonical-plan-id"}

    conn = get_db()
    row = conn.execute(
        "SELECT delivery_status, canonical_plan_id, canonical_done_at, "
        "action, session_id, canonical_dispatched_at, canonical_actions_json "
        "FROM lifecycle_events WHERE event_id = ?",
        (str(event_id),),
    ).fetchone()
    if row is None:
        return {"status": "rejected", "reason": "unknown-event-id"}
    current_status = str(row[0] or "")
    expected_plan = row[1]
    already_done_at = row[2]
    action = str(row[3] or "")
    session_id = str(row[4] or "")
    dispatched_at = row[5]
    actions_json = row[6]

    if expected_plan and canonical_plan_id != expected_plan:
        return {
            "status": "rejected",
            "reason": "canonical_plan_id-mismatch",
            "expected": expected_plan,
            "received": canonical_plan_id,
        }
    if already_done_at and current_status == "canonical_done":
        return {
            "status": "already_processed",
            "event_id": event_id,
            "duplicate": True,
        }

    results_list = list(results or [])
    any_failure = any(
        str((r or {}).get("status", "")).lower() not in {"ok", "success", "already_processed"}
        for r in results_list
    )
    diary_evidence = _session_diary_evidence(conn, session_id, dispatched_at, actions_json)
    stop_state = _session_stop_state(conn, session_id) if session_id else {
        "verifiable": False,
        "session_registered": False,
        "stop_confirmed": True,
        "active_session_ids": [],
    }
    diary_required = action in _DIARY_TRIGGERING and bool(session_id)
    session_registered = _session_is_linked_to_nexo(conn, session_id) if diary_required else bool(session_id)
    session_unregistered = diary_required and not session_registered
    diary_missing = diary_required and diary_evidence is None
    stop_missing = diary_required and bool(stop_state.get("verifiable")) and not bool(stop_state.get("stop_confirmed"))
    effective = "retryable_error" if (any_failure or diary_missing or stop_missing) else "canonical_done"
    last_error = None
    if session_unregistered:
        last_error = SESSION_NOT_LINKED_REASON
    elif any_failure:
        last_error = "one-or-more-actions-failed"
    elif diary_missing:
        last_error = "canonical-diary-not-confirmed"
    elif stop_missing:
        last_error = "canonical-stop-not-confirmed"
    conn.execute(
        "UPDATE lifecycle_events "
        "SET delivery_status = ?, "
        "    canonical_done_at = CASE WHEN ? = 'canonical_done' THEN datetime('now') ELSE NULL END, "
        "    canonical_done_results = ?, "
        "    last_error = ? "
        "WHERE event_id = ?",
        (
            effective,
            effective,
            json.dumps(results_list, ensure_ascii=False),
            last_error,
            str(event_id),
        ),
    )
    conn.commit()
    return {
        "status": effective,
        "event_id": event_id,
        "canonical_plan_id": canonical_plan_id,
        "failed_actions": any_failure,
        "diary_confirmed": diary_evidence is not None,
        "diary_required": diary_required,
        "stop_confirmed": not stop_missing,
        "stop_required": diary_required,
        "session_registered": session_registered,
        "session_diary_id": diary_evidence.get("session_diary_id") if diary_evidence else None,
        "active_session_ids": list(stop_state.get("active_session_ids") or []),
        "reason": last_error if effective == "retryable_error" else None,
    }


def wait_for_canonical_diary(
    event_id: str,
    timeout_ms: int = 45_000,
    poll_ms: int = 500,
) -> Dict[str, Any]:
    """Poll until the lifecycle event has concrete session_diary evidence."""
    if not event_id:
        return {"status": "rejected", "reason": "missing-event-id"}
    timeout_s = max(0.0, float(timeout_ms or 0) / 1000.0)
    poll_s = max(0.05, float(poll_ms or 500) / 1000.0)
    deadline = time.monotonic() + timeout_s
    last_error: Optional[str] = None

    while True:
        conn = get_db()
        row = conn.execute(
            "SELECT session_id, canonical_dispatched_at, canonical_actions_json "
            "FROM lifecycle_events WHERE event_id = ?",
            (str(event_id),),
        ).fetchone()
        if row is None:
            return {"status": "rejected", "reason": "unknown-event-id", "event_id": event_id}
        session_id = str(row[0] or "")
        if not session_id:
            return {"status": "rejected", "reason": "missing-session-id", "event_id": event_id}
        if not _session_is_linked_to_nexo(conn, session_id):
            return {
                "status": "retryable_error",
                "event_id": event_id,
                "session_id": session_id,
                "diary_confirmed": False,
                "session_registered": False,
                "reason": SESSION_NOT_LINKED_REASON,
            }
        evidence = _session_diary_evidence(conn, session_id, row[1], row[2])
        if evidence is not None:
            return {
                "status": "ok",
                "event_id": event_id,
                "session_id": session_id,
                "diary_confirmed": True,
                "session_registered": True,
                **evidence,
            }
        if time.monotonic() >= deadline:
            return {
                "status": "retryable_error",
                "event_id": event_id,
                "session_id": session_id,
                "diary_confirmed": False,
                "session_registered": True,
                "reason": last_error or "diary-confirm-timeout",
            }
        time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))


def wait_for_canonical_stop(
    event_id: str,
    timeout_ms: int = 10_000,
    poll_ms: int = 500,
) -> Dict[str, Any]:
    """Poll until the canonical lifecycle session no longer has active linked SIDs."""
    if not event_id:
        return {"status": "rejected", "reason": "missing-event-id"}

    timeout_s = max(0.0, float(timeout_ms or 0) / 1000.0)
    poll_s = max(0.05, float(poll_ms or 500) / 1000.0)
    deadline = time.monotonic() + timeout_s

    while True:
        conn = get_db()
        row = conn.execute(
            "SELECT session_id, action FROM lifecycle_events WHERE event_id = ?",
            (str(event_id),),
        ).fetchone()
        if row is None:
            return {"status": "rejected", "reason": "unknown-event-id", "event_id": event_id}

        session_id = str(row[0] or "")
        action = str(row[1] or "")
        stop_required = action in _DIARY_TRIGGERING and bool(session_id)
        if not stop_required:
            return {
                "status": "ok",
                "event_id": event_id,
                "stop_required": False,
                "stop_confirmed": True,
                "session_registered": bool(session_id),
                "active_session_ids": [],
            }

        stop_state = _session_stop_state(conn, session_id)
        if not stop_state.get("verifiable"):
            return {
                "status": "retryable_error",
                "event_id": event_id,
                "stop_required": True,
                "stop_confirmed": False,
                "session_registered": False,
                "active_session_ids": [],
                "reason": SESSION_NOT_LINKED_REASON,
            }
        if stop_state.get("stop_confirmed"):
            return {
                "status": "ok",
                "event_id": event_id,
                "stop_required": True,
                "stop_confirmed": True,
                "session_registered": True,
                "active_session_ids": [],
            }
        if time.monotonic() >= deadline:
            return {
                "status": "retryable_error",
                "event_id": event_id,
                "stop_required": True,
                "stop_confirmed": False,
                "session_registered": True,
                "active_session_ids": list(stop_state.get("active_session_ids") or []),
                "reason": "canonical-stop-not-confirmed",
            }
        time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))


def get_lifecycle_event(event_id: str) -> Optional[Dict[str, Any]]:
    if not event_id:
        return None
    row = get_db().execute(
        "SELECT event_id, schema_version, source, action, conversation_id, "
        "session_id, reason, payload_snapshot, delivery_status, retry_count, "
        "created_at, processed_at, last_error, "
        "canonical_plan_id, canonical_plan_version, canonical_actions_json, "
        "canonical_dispatched_at, canonical_done_at, canonical_done_results "
        "FROM lifecycle_events WHERE event_id = ?",
        (str(event_id),),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row[7] or "{}")
    except Exception:
        payload = {}
    try:
        actions = json.loads(row[15]) if row[15] else None
    except Exception:
        actions = None
    try:
        results = json.loads(row[18]) if row[18] else None
    except Exception:
        results = None
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
        "canonical_plan_id": row[13],
        "canonical_plan_version": row[14],
        "canonical_actions": actions,
        "canonical_dispatched_at": row[16],
        "canonical_done_at": row[17],
        "canonical_done_results": results,
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
