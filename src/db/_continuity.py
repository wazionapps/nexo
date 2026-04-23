from __future__ import annotations

"""Durable continuity snapshots used by Desktop and compaction recovery."""

import hashlib
import json
from datetime import datetime, timezone

from db._core import get_db


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_payload(payload) -> dict:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return {}
        try:
            loaded = json.loads(text)
        except Exception:
            return {"raw": text}
        return loaded if isinstance(loaded, dict) else {"value": loaded}
    return {}


def build_snapshot_idempotency_key(
    *,
    conversation_id: str,
    session_id: str = "",
    event_type: str = "",
    trace_id: str = "",
    payload=None,
) -> str:
    normalized = json.dumps(_normalize_payload(payload), sort_keys=True, ensure_ascii=False)
    seed = "|".join(
        [
            str(conversation_id or "").strip(),
            str(session_id or "").strip(),
            str(event_type or "").strip(),
            str(trace_id or "").strip(),
            normalized,
        ]
    )
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def write_continuity_snapshot(
    *,
    conversation_id: str,
    session_id: str = "",
    external_session_id: str = "",
    client: str = "",
    event_type: str = "turn_end",
    payload=None,
    trace_id: str = "",
    idempotency_key: str = "",
) -> dict:
    conversation_id = str(conversation_id or "").strip()
    if not conversation_id:
        raise ValueError("conversation_id is required")

    payload_dict = _normalize_payload(payload)
    idem = str(idempotency_key or "").strip() or build_snapshot_idempotency_key(
        conversation_id=conversation_id,
        session_id=session_id,
        event_type=event_type,
        trace_id=trace_id,
        payload=payload_dict,
    )
    conn = get_db()
    conn.execute(
        """
        INSERT INTO continuity_snapshots (
            conversation_id, session_id, external_session_id, client,
            event_type, payload_json, trace_id, idempotency_key, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(conversation_id, idempotency_key) DO UPDATE SET
            session_id = excluded.session_id,
            external_session_id = excluded.external_session_id,
            client = excluded.client,
            event_type = excluded.event_type,
            payload_json = excluded.payload_json,
            trace_id = excluded.trace_id,
            updated_at = excluded.updated_at
        """,
        (
            conversation_id,
            str(session_id or "").strip(),
            str(external_session_id or "").strip(),
            str(client or "").strip(),
            str(event_type or "turn_end").strip(),
            json.dumps(payload_dict, ensure_ascii=False),
            str(trace_id or "").strip(),
            idem,
            _utc_now(),
            _utc_now(),
        ),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT id, conversation_id, session_id, external_session_id, client,
               event_type, payload_json, trace_id, idempotency_key, created_at, updated_at
        FROM continuity_snapshots
        WHERE conversation_id = ? AND idempotency_key = ?
        LIMIT 1
        """,
        (conversation_id, idem),
    ).fetchone()
    snapshot = dict(row) if row else {}
    try:
        snapshot["payload"] = json.loads(snapshot.get("payload_json") or "{}")
    except Exception:
        snapshot["payload"] = {}
    return snapshot


def list_continuity_snapshots(
    *,
    conversation_id: str = "",
    session_id: str = "",
    limit: int = 20,
) -> list[dict]:
    conn = get_db()
    clauses = []
    params: list[object] = []
    if conversation_id:
        clauses.append("conversation_id = ?")
        params.append(str(conversation_id).strip())
    if session_id:
        clauses.append("session_id = ?")
        params.append(str(session_id).strip())
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"""
        SELECT id, conversation_id, session_id, external_session_id, client,
               event_type, payload_json, trace_id, idempotency_key, created_at, updated_at
        FROM continuity_snapshots
        {where}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, max(1, int(limit or 20))),
    ).fetchall()
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.get("payload_json") or "{}")
        except Exception:
            item["payload"] = {}
        result.append(item)
    return result


def latest_continuity_snapshot(
    *,
    conversation_id: str = "",
    session_id: str = "",
) -> dict | None:
    rows = list_continuity_snapshots(
        conversation_id=conversation_id,
        session_id=session_id,
        limit=1,
    )
    return rows[0] if rows else None
