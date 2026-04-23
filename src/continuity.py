from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from db import (
    get_db,
    latest_continuity_snapshot,
    list_continuity_snapshots,
    read_checkpoint,
    write_continuity_snapshot,
)
from tools_sessions import _session_portability_bundle


RESUME_BUNDLE_TOKEN_BUDGET_DEFAULT = 2000
UNSAFE_SID_STALE_TTL_SECONDS = 30 * 60
RECENT_DIARY_WINDOW_MINUTES = 30


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _estimate_tokens(value) -> int:
    if value is None:
        return 0
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return max(1, int(len(value) / 4))


def _recent_diary_for_session(session_id: str = "") -> dict:
    sid = str(session_id or "").strip()
    if not sid:
        return {}
    conn = get_db()
    row = conn.execute(
        """
        SELECT summary, decisions, pending, context_next, created_at
        FROM session_diary
        WHERE session_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (sid,),
    ).fetchone()
    if not row:
        return {}
    item = dict(row)
    created_dt = _parse_dt(str(item.get("created_at") or ""))
    if created_dt:
        window_start = datetime.now(timezone.utc) - timedelta(minutes=RECENT_DIARY_WINDOW_MINUTES)
        item["within_recent_window"] = created_dt >= window_start
    return item


def _resolve_session_row(sid: str = "", conversation_id: str = "") -> dict | None:
    conn = get_db()
    sid = str(sid or "").strip()
    conversation_id = str(conversation_id or "").strip()
    if sid:
        row = conn.execute(
            """
            SELECT sid, task, started_epoch, last_update_epoch, local_time,
                   claude_session_id, external_session_id, session_client, conversation_id
            FROM sessions
            WHERE sid = ?
            LIMIT 1
            """,
            (sid,),
        ).fetchone()
        if row:
            return dict(row)
    if conversation_id:
        row = conn.execute(
            """
            SELECT sid, task, started_epoch, last_update_epoch, local_time,
                   claude_session_id, external_session_id, session_client, conversation_id
            FROM sessions
            WHERE conversation_id = ?
            ORDER BY last_update_epoch DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
        if row:
            return dict(row)
    return None


def _active_session_rows_for_conversation(conversation_id: str) -> list[dict]:
    conv = str(conversation_id or "").strip()
    if not conv:
        return []
    conn = get_db()
    cutoff = time.time() - 900
    rows = conn.execute(
        """
        SELECT sid, task, started_epoch, last_update_epoch, external_session_id,
               session_client, conversation_id
        FROM sessions
        WHERE conversation_id = ? AND last_update_epoch > ?
        ORDER BY last_update_epoch DESC
        """,
        (conv, cutoff),
    ).fetchall()
    return [dict(row) for row in rows]


def _resolve_unsafe_state(
    *,
    conversation_id: str = "",
    session_id: str = "",
    external_session_id: str = "",
    client: str = "",
) -> tuple[bool, list[str], dict | None, dict | None]:
    conv = str(conversation_id or "").strip()
    sid = str(session_id or "").strip()
    external = str(external_session_id or "").strip()
    client_label = str(client or "").strip()

    session_row = _resolve_session_row(sid=sid, conversation_id=conv)
    latest_snapshot = latest_continuity_snapshot(conversation_id=conv, session_id=sid)
    reasons: list[str] = []

    if sid and session_row is None and latest_snapshot is None:
        reasons.append("sid_not_found")
    active_rows = _active_session_rows_for_conversation(conv) if conv else []
    active_sids = {row["sid"] for row in active_rows if row.get("sid")}
    if conv and len(active_sids) > 1:
        reasons.append("conversation_has_multiple_active_sessions")
    if session_row and conv and session_row.get("conversation_id") and session_row["conversation_id"] != conv:
        reasons.append("conversation_id_mismatch")
    if session_row and external and session_row.get("external_session_id") and session_row["external_session_id"] != external:
        reasons.append("external_session_id_mismatch")
    if latest_snapshot and client_label:
        snapshot_client = str(latest_snapshot.get("client") or "").strip()
        if snapshot_client and snapshot_client != client_label:
            reasons.append("client_mismatch")
    if latest_snapshot and session_row and sid:
        snap_dt = _parse_dt(str(latest_snapshot.get("created_at") or "")) or _parse_dt(str(latest_snapshot.get("updated_at") or ""))
        if snap_dt and session_row.get("started_epoch"):
            start_dt = datetime.fromtimestamp(float(session_row["started_epoch"]), tz=timezone.utc)
            if (snap_dt - start_dt).total_seconds() > UNSAFE_SID_STALE_TTL_SECONDS and session_row.get("last_update_epoch", 0) < session_row.get("started_epoch", 0):
                reasons.append("startup_older_than_snapshot_ttl")

    return (len(reasons) > 0), reasons, session_row, latest_snapshot


def _build_resume_lines(bundle: dict) -> str:
    lines = [
        "[NEXO Continuity Resume]",
        f"conversation_id={bundle.get('conversation_id', '')}",
    ]
    if bundle.get("session_id"):
        lines.append(f"session_id={bundle['session_id']}")
    objective = str(bundle.get("objective") or "").strip()
    if objective:
        lines.append(f"objective={objective}")
    pending = bundle.get("pending") or []
    if pending:
        lines.append("pending:")
        for item in pending[:5]:
            lines.append(f"- {item}")
    decisions = bundle.get("decisions") or []
    if decisions:
        lines.append("recent_decisions:")
        for item in decisions[:4]:
            lines.append(f"- {item}")
    errors = bundle.get("recent_errors") or []
    if errors:
        lines.append("recent_errors:")
        for item in errors[:3]:
            lines.append(f"- {item}")
    transcript_tail = bundle.get("transcript_tail") or []
    if transcript_tail:
        lines.append("transcript_tail:")
        for item in transcript_tail[:4]:
            lines.append(f"- {item}")
    hot_context = str(bundle.get("hot_context") or "").strip()
    if hot_context:
        lines.append("hot_context:")
        lines.append(hot_context)
    diary = str(bundle.get("latest_diary_summary") or "").strip()
    if diary:
        lines.append(f"latest_diary={diary}")
    return "\n".join(lines)


def _truncate_bundle(bundle: dict, *, token_budget: int) -> dict:
    current = dict(bundle)
    for field in ["latest_diary_summary", "hot_context", "transcript_tail", "recent_errors", "decisions", "pending"]:
        rendered = _build_resume_lines(current)
        if _estimate_tokens(rendered) <= token_budget:
            current["size_tokens_estimated"] = _estimate_tokens(rendered)
            current["resume_text"] = rendered
            return current
        value = current.get(field)
        if isinstance(value, list):
            current[field] = value[: max(0, len(value) // 2)]
        elif isinstance(value, str):
            current[field] = value[: max(120, int(len(value) * 0.5))]
    rendered = _build_resume_lines(current)
    current["size_tokens_estimated"] = _estimate_tokens(rendered)
    current["resume_text"] = rendered
    return current


def write_snapshot(
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
    unsafe, reasons, _session_row, _latest = _resolve_unsafe_state(
        conversation_id=conversation_id,
        session_id=session_id,
        external_session_id=external_session_id,
        client=client,
    )
    if unsafe and "conversation_has_multiple_active_sessions" in reasons:
        return {
            "ok": False,
            "unsafe_sid": True,
            "reasons": reasons,
            "conversation_id": str(conversation_id or "").strip(),
            "session_id": str(session_id or "").strip(),
        }
    snapshot = write_continuity_snapshot(
        conversation_id=conversation_id,
        session_id=session_id,
        external_session_id=external_session_id,
        client=client,
        event_type=event_type,
        payload=payload,
        trace_id=trace_id,
        idempotency_key=idempotency_key,
    )
    return {
        "ok": True,
        "unsafe_sid": False,
        "conversation_id": snapshot.get("conversation_id"),
        "session_id": snapshot.get("session_id"),
        "snapshot_id": snapshot.get("id"),
        "trace_id": snapshot.get("trace_id") or trace_id,
        "idempotency_key": snapshot.get("idempotency_key"),
        "event_type": snapshot.get("event_type"),
    }


def read_snapshot(
    *,
    conversation_id: str = "",
    session_id: str = "",
    limit: int = 20,
) -> dict:
    rows = list_continuity_snapshots(
        conversation_id=conversation_id,
        session_id=session_id,
        limit=limit,
    )
    return {
        "ok": True,
        "conversation_id": str(conversation_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "count": len(rows),
        "items": rows,
    }


def build_resume_bundle(
    *,
    conversation_id: str = "",
    session_id: str = "",
    external_session_id: str = "",
    client: str = "",
    token_budget: int = RESUME_BUNDLE_TOKEN_BUDGET_DEFAULT,
) -> dict:
    unsafe, reasons, session_row, latest_snapshot = _resolve_unsafe_state(
        conversation_id=conversation_id,
        session_id=session_id,
        external_session_id=external_session_id,
        client=client,
    )
    conv = str(conversation_id or (session_row or {}).get("conversation_id") or (latest_snapshot or {}).get("conversation_id") or "").strip()
    sid = str(session_id or (session_row or {}).get("sid") or (latest_snapshot or {}).get("session_id") or "").strip()

    if unsafe:
        return {
            "ok": True,
            "unsafe_sid": True,
            "conversation_id": conv,
            "session_id": sid,
            "reasons": reasons,
            "bundle": {},
            "resume_text": "",
        }

    portability = _session_portability_bundle(sid) if sid else {"ok": False}
    checkpoint = dict(read_checkpoint(sid) or {}) if sid else {}
    diary = _recent_diary_for_session(sid)
    snapshot_payload = dict((latest_snapshot or {}).get("payload") or {})
    transcript_tail = snapshot_payload.get("transcript_tail") or snapshot_payload.get("messages") or []
    if isinstance(transcript_tail, list):
        transcript_tail = [str(item).strip() for item in transcript_tail if str(item).strip()][:8]
    else:
        transcript_tail = [str(transcript_tail).strip()] if str(transcript_tail).strip() else []

    objective = (
        snapshot_payload.get("current_goal")
        or snapshot_payload.get("goal")
        or checkpoint.get("current_goal")
        or (portability.get("session") or {}).get("task")
        or ""
    )
    raw_pending = diary.get("pending") or snapshot_payload.get("pending") or checkpoint.get("next_step") or ""
    if isinstance(raw_pending, list):
        pending_items = [str(item).strip() for item in raw_pending if str(item).strip()]
    else:
        pending_items = [part.strip(" -") for part in str(raw_pending).splitlines() if part.strip()]
    checkpoint_next = str(checkpoint.get("next_step") or "").strip()
    if checkpoint_next and checkpoint_next not in pending_items:
        pending_items.insert(0, checkpoint_next)

    raw_decisions = diary.get("decisions") or checkpoint.get("decisions_summary") or snapshot_payload.get("decisions") or ""
    if isinstance(raw_decisions, list):
        decisions = [str(item).strip() for item in raw_decisions if str(item).strip()]
    else:
        decisions = [part.strip(" -") for part in str(raw_decisions).splitlines() if part.strip()]

    raw_errors = checkpoint.get("errors_found") or snapshot_payload.get("recent_errors") or ""
    if isinstance(raw_errors, list):
        recent_errors = [str(item).strip() for item in raw_errors if str(item).strip()]
    else:
        recent_errors = [part.strip(" -") for part in str(raw_errors).splitlines() if part.strip()]

    hot_context = portability.get("recent_context") if portability.get("ok") else {}
    bundle = {
        "ok": True,
        "unsafe_sid": False,
        "bundle_version": 1,
        "schema_version": 1,
        "generated_at": _utc_now(),
        "conversation_id": conv,
        "session_id": sid,
        "identity": {
            "conversation_id": conv,
            "session_id": sid,
            "external_session_id": (session_row or {}).get("external_session_id", ""),
            "client": client or (session_row or {}).get("session_client", "") or (latest_snapshot or {}).get("client", ""),
        },
        "objective": str(objective or "").strip(),
        "pending": pending_items,
        "decisions": decisions,
        "recent_errors": recent_errors,
        "transcript_tail": transcript_tail,
        "hot_context": json.dumps(hot_context, ensure_ascii=False) if hot_context else "",
        "latest_diary_summary": str(diary.get("summary") or "").strip(),
        "trace_id": (latest_snapshot or {}).get("trace_id", ""),
    }
    return _truncate_bundle(bundle, token_budget=max(400, int(token_budget or RESUME_BUNDLE_TOKEN_BUDGET_DEFAULT)))


def record_compaction_event(
    *,
    conversation_id: str,
    session_id: str = "",
    payload=None,
    trace_id: str = "",
    event_type: str = "post_compact",
) -> dict:
    return write_snapshot(
        conversation_id=conversation_id,
        session_id=session_id,
        client="brain",
        event_type=event_type,
        payload=payload,
        trace_id=trace_id,
    )


def continuity_audit(
    *,
    conversation_id: str,
    limit: int = 50,
) -> dict:
    conv = str(conversation_id or "").strip()
    items = list_continuity_snapshots(conversation_id=conv, limit=limit)
    session_ids = [item.get("session_id") for item in items if item.get("session_id")]
    diaries = []
    if session_ids:
        conn = get_db()
        placeholders = ",".join("?" for _ in session_ids)
        rows = conn.execute(
            f"""
            SELECT session_id, created_at, summary, decisions, pending
            FROM session_diary
            WHERE session_id IN ({placeholders})
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*session_ids, max(1, int(limit or 50))),
        ).fetchall()
        diaries = [dict(row) for row in rows]
    return {
        "ok": True,
        "conversation_id": conv,
        "snapshot_count": len(items),
        "items": items,
        "diaries": diaries,
    }


def format_bundle_text(bundle: dict) -> str:
    if bundle.get("unsafe_sid"):
        reasons = ", ".join(bundle.get("reasons") or []) or "unknown"
        return f"unsafe_sid=true ({reasons})"
    return str(bundle.get("resume_text") or "").strip()


def json_dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)
