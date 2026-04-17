from __future__ import annotations
"""NEXO DB — Reminders and followups with history + soft delete."""

import datetime
import json
import secrets
import sqlite3
from typing import Any

from db._core import get_db, now_epoch
from db._classification import classify_task, normalise_internal, normalise_owner
from db._fts import fts_upsert
from db._hot_context import capture_context_event

ACTIVE_EXCLUDED_STATUSES = {"DELETED", "archived", "blocked", "waiting"}
READ_TOKEN_TTL_SECONDS = 30 * 60

# Opportunistic cleanup of expired item_read_tokens: runs at most once every
# _READ_TOKEN_PURGE_INTERVAL seconds from inside _issue_item_read_token. This
# avoids unbounded growth of expired tokens without adding a new cron or
# relying on maintenance_schedule (which is currently not wired up — its
# runner check_and_run_overdue is defined but never invoked from anywhere).
_READ_TOKEN_PURGE_INTERVAL = 3600  # 1 hour
_last_read_token_purge: float = 0.0


def _purge_expired_read_tokens_if_due(conn: sqlite3.Connection, now: float) -> None:
    """Delete expired item_read_tokens in-band with a 1h throttle.

    Called from _issue_item_read_token so cleanup rides on normal activity and
    does not require a separate scheduler. Failures are swallowed because
    token issuance must never be blocked by cleanup problems.
    """
    global _last_read_token_purge
    if now - _last_read_token_purge < _READ_TOKEN_PURGE_INTERVAL:
        return
    _last_read_token_purge = now
    try:
        conn.execute(
            "DELETE FROM item_read_tokens WHERE expires_at < ?",
            (now,),
        )
    except Exception:
        # Cleanup must never block token issuance. Swallow and move on.
        pass


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _serialize_metadata(metadata: dict[str, Any] | None) -> str:
    if not metadata:
        return "{}"
    try:
        return json.dumps(metadata, ensure_ascii=True, sort_keys=True)
    except Exception:
        return "{}"


def _truncate(text: str | None, limit: int = 240) -> str:
    if not text:
        return ""
    text = str(text).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _format_changes(before: sqlite3.Row | dict | None, after: sqlite3.Row | dict | None, fields: list[str]) -> str:
    if before is None or after is None:
        return ""
    changes: list[str] = []
    before_d = dict(before)
    after_d = dict(after)
    for field in fields:
        old = before_d.get(field)
        new = after_d.get(field)
        if old == new:
            continue
        changes.append(f"{field}: {_truncate(old, 60) or '∅'} -> {_truncate(new, 60) or '∅'}")
    return "; ".join(changes)


def _item_table(item_type: str) -> str:
    if item_type == "reminder":
        return "reminders"
    if item_type == "followup":
        return "followups"
    raise ValueError(f"Unsupported item_type: {item_type}")


def _history_rules(item_type: str) -> list[str]:
    label = "followup" if item_type == "followup" else "reminder"
    return [
        f"Read this {label} and its history before update/delete/restore via MCP.",
        f"Delete is soft: the {label} stays in the DB with status DELETED.",
        f"Use notes to append operational context instead of overwriting history.",
    ]


def _latest_history_seq(conn, item_type: str, item_id: str) -> int:
    if not _table_exists(conn, "item_history"):
        return 0
    row = conn.execute(
        "SELECT MAX(id) AS max_id FROM item_history WHERE item_type = ? AND item_id = ?",
        (item_type, item_id),
    ).fetchone()
    return int(row["max_id"] or 0)


def add_item_history(
    item_type: str,
    item_id: str,
    event_type: str,
    note: str = "",
    *,
    actor: str = "system",
    metadata: dict[str, Any] | None = None,
    created_at: float | None = None,
) -> dict:
    """Append an event to reminder/followup history."""
    conn = get_db()
    if not _table_exists(conn, "item_history"):
        return {
            "item_type": item_type,
            "item_id": item_id,
            "event_type": event_type,
            "note": note or "",
            "actor": actor,
            "metadata": _serialize_metadata(metadata),
            "created_at": created_at if created_at is not None else now_epoch(),
            "skipped": True,
        }
    ts = created_at if created_at is not None else now_epoch()
    conn.execute(
        "INSERT INTO item_history (item_type, item_id, event_type, note, actor, metadata, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (item_type, item_id, event_type, note or "", actor, _serialize_metadata(metadata), ts),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM item_history WHERE item_type = ? AND item_id = ? ORDER BY id DESC LIMIT 1",
        (item_type, item_id),
    ).fetchone()
    return dict(row)


def get_item_history(item_type: str, item_id: str, limit: int = 20) -> list[dict]:
    """Return latest history events for a reminder/followup."""
    conn = get_db()
    if not _table_exists(conn, "item_history"):
        return []
    rows = conn.execute(
        "SELECT * FROM item_history WHERE item_type = ? AND item_id = ? ORDER BY id DESC LIMIT ?",
        (item_type, item_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _issue_item_read_token(item_type: str, item_id: str, ttl_seconds: int = READ_TOKEN_TTL_SECONDS) -> str:
    conn = get_db()
    now = now_epoch()
    # Opportunistic cleanup of expired tokens so the table does not grow
    # unbounded. Throttled to once per hour. Wrapped defensively: any
    # failure inside the cleanup helper must never block token issuance.
    try:
        _purge_expired_read_tokens_if_due(conn, now)
    except Exception:
        pass
    token = "IRT-" + secrets.token_hex(12)
    history_seq = _latest_history_seq(conn, item_type, item_id)
    conn.execute(
        "INSERT INTO item_read_tokens (token, item_type, item_id, history_seq, issued_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token, item_type, item_id, history_seq, now, now + ttl_seconds),
    )
    conn.commit()
    return token


def validate_item_read_token(token: str, item_type: str, item_id: str) -> tuple[bool, str]:
    """Validate that an item was read recently enough before mutation."""
    if not token:
        return False, "Missing read_token. Call the corresponding *_get tool first and use its READ_TOKEN."

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM item_read_tokens WHERE token = ? AND item_type = ? AND item_id = ?",
        (token, item_type, item_id),
    ).fetchone()
    if not row:
        return False, "Invalid read_token. Call the corresponding *_get tool again."

    now = now_epoch()
    if float(row["expires_at"] or 0) < now:
        conn.execute("DELETE FROM item_read_tokens WHERE token = ?", (token,))
        conn.commit()
        return False, "Expired read_token. Read the item again to refresh its history context."

    current_seq = _latest_history_seq(conn, item_type, item_id)
    if current_seq != int(row["history_seq"] or 0):
        return False, "History changed since that read. Read the item again before mutating it."

    return True, ""


def _reassign_item_identity(conn, item_type: str, old_id: str, new_id: str):
    if old_id == new_id:
        return
    conn.execute(
        "UPDATE item_history SET item_id = ? WHERE item_type = ? AND item_id = ?",
        (new_id, item_type, old_id),
    )
    conn.execute(
        "UPDATE item_read_tokens SET item_id = ? WHERE item_type = ? AND item_id = ?",
        (new_id, item_type, old_id),
    )


def _active_status_where(column_name: str = "status") -> str:
    excluded = ", ".join(f"'{value}'" for value in sorted(ACTIVE_EXCLUDED_STATUSES))
    return (
        f"{column_name} NOT LIKE 'COMPLETED%' "
        f"AND {column_name} NOT IN ({excluded})"
    )


def _context_state_from_status(status: str | None) -> str:
    normalized = str(status or "PENDING").strip().upper()
    if normalized.startswith("COMPLETED"):
        return "resolved"
    if normalized == "DELETED":
        return "abandoned"
    if normalized == "WAITING":
        return "waiting_user"
    if normalized == "BLOCKED":
        return "blocked"
    return "active"


# ── Reminders ──────────────────────────────────────────────────────


def create_reminder(
    id: str,
    description: str,
    date: str = None,
    status: str = "PENDING",
    category: str = "general",
    internal: object = None,
    owner: str | None = None,
) -> dict:
    """Create a new reminder.

    Agents may pass `internal` (0/1, bool, or string) and `owner`
    ('user'|'waiting'|'agent'|'shared') to override the default
    classification. When omitted, classify_task() applies the legacy
    heuristic so behaviour matches pre-migration #40.
    """
    conn = get_db()
    now = now_epoch()

    auto_internal, auto_owner = classify_task(id, description, category, None)
    internal_value = normalise_internal(internal)
    if internal_value is None:
        internal_value = auto_internal
    owner_value = normalise_owner(owner) or auto_owner

    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(reminders)").fetchall()}
    payload: dict[str, object] = {
        "id": id,
        "date": date,
        "description": description,
        "status": status,
        "category": category,
        "created_at": now,
        "updated_at": now,
    }
    if "internal" in columns:
        payload["internal"] = internal_value
    if "owner" in columns:
        payload["owner"] = owner_value

    insert_columns = [c for c in payload if c in columns]
    placeholders = ", ".join("?" for _ in insert_columns)

    try:
        conn.execute(
            f"INSERT INTO reminders ({', '.join(insert_columns)}) VALUES ({placeholders})",
            [payload[c] for c in insert_columns],
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return {"error": f"Reminder {id} already exists. Use update instead."}

    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    add_item_history(
        "reminder",
        id,
        "created",
        note=f"Reminder created. Category={category}. Date={date or '—'}. Owner={owner_value}.",
        actor="db",
    )
    capture_context_event(
        event_type="reminder_created",
        title=description[:160],
        summary=description[:600],
        body=f"Category={category}. Date={date or '—'}.",
        context_key=f"reminder:{id}",
        context_title=description[:160],
        context_summary=description[:600],
        context_type="reminder",
        state=_context_state_from_status(status),
        owner="user",
        actor="db",
        source_type="reminder",
        source_id=id,
        metadata={
            "category": category,
            "status": status,
            "date": date or "",
            "internal": internal_value,
            "owner": owner_value,
        },
        ttl_hours=24,
    )
    return dict(row)


def update_reminder(
    id: str,
    *,
    log_history: bool = True,
    history_event: str = "updated",
    history_actor: str = "db",
    history_note: str = "",
    **kwargs,
) -> dict:
    """Update any fields of a reminder: description, date, status, category."""
    conn = get_db()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    if not row:
        return {"error": f"Reminder {id} not found"}

    allowed = {"description", "date", "status", "category", "internal", "owner"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if "internal" in updates:
        coerced = normalise_internal(updates["internal"])
        if coerced is None:
            updates.pop("internal")
        else:
            updates["internal"] = coerced
    if "owner" in updates:
        coerced = normalise_owner(updates["owner"])
        if coerced is None:
            updates.pop("owner")
        else:
            updates["owner"] = coerced
    if not updates:
        return {"error": "No valid fields to update"}

    table_columns = {
        str(r["name"]) for r in conn.execute("PRAGMA table_info(reminders)").fetchall()
    }
    updates = {k: v for k, v in updates.items() if k in table_columns or k == "updated_at"}

    updates["updated_at"] = now_epoch()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE reminders SET {set_clause} WHERE id = ?", values)
    conn.commit()

    new_row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    current = dict(new_row) if new_row else dict(row)
    if log_history:
        note = history_note or _format_changes(row, new_row, ["description", "date", "status", "category"])
        add_item_history("reminder", id, history_event, note=note or "Reminder updated.", actor=history_actor)
    capture_context_event(
        event_type=f"reminder_{history_event}",
        title=(new_row["description"] if new_row else row["description"])[:160],
        summary=((note if log_history else history_note) or "Reminder updated.")[:600],
        body=((new_row["description"] if new_row else row["description"]) or "")[:1600],
        context_key=f"reminder:{id}",
        context_title=(new_row["description"] if new_row else row["description"])[:160],
        context_summary=((new_row["description"] if new_row else row["description"]) or "")[:600],
        context_type="reminder",
        state=_context_state_from_status(current.get("status")),
        owner="user",
        actor=history_actor,
        source_type="reminder",
        source_id=id,
        metadata={"status": current.get("status", ""), "date": current.get("date", "")},
        ttl_hours=24,
    )
    return dict(new_row)


def complete_reminder(id: str) -> dict:
    """Mark a reminder as completed."""
    result = update_reminder(
        id,
        status="COMPLETED",
        log_history=False,
    )
    if "error" in result:
        return result
    add_item_history("reminder", id, "completed", note="Reminder marked COMPLETED.", actor="db")
    capture_context_event(
        event_type="reminder_completed",
        title=(result.get("description") or id)[:160],
        summary="Reminder marked COMPLETED.",
        body=(result.get("description") or "")[:1600],
        context_key=f"reminder:{id}",
        context_title=(result.get("description") or id)[:160],
        context_summary=(result.get("description") or "")[:600],
        context_type="reminder",
        state="resolved",
        owner="user",
        actor="db",
        source_type="reminder",
        source_id=id,
        metadata={"status": "COMPLETED"},
        ttl_hours=24,
    )
    return result


def delete_reminder(id: str) -> bool:
    """Soft-delete a reminder by setting status to DELETED."""
    result = update_reminder(
        id,
        status="DELETED",
        log_history=False,
    )
    if "error" in result:
        return False
    add_item_history("reminder", id, "deleted", note="Reminder soft-deleted (status=DELETED).", actor="db")
    capture_context_event(
        event_type="reminder_deleted",
        title=(result.get("description") or id)[:160],
        summary="Reminder soft-deleted (status=DELETED).",
        body=(result.get("description") or "")[:1600],
        context_key=f"reminder:{id}",
        context_title=(result.get("description") or id)[:160],
        context_summary=(result.get("description") or "")[:600],
        context_type="reminder",
        state="abandoned",
        owner="user",
        actor="db",
        source_type="reminder",
        source_id=id,
        metadata={"status": "DELETED"},
        ttl_hours=24,
    )
    return True


def restore_reminder(id: str) -> dict:
    """Restore a soft-deleted reminder back to PENDING."""
    row = get_reminder(id)
    if not row:
        return {"error": f"Reminder {id} not found"}
    result = update_reminder(
        id,
        status="PENDING",
        log_history=False,
    )
    if "error" in result:
        return result
    previous = row.get("status") or "unknown"
    add_item_history("reminder", id, "restored", note=f"Reminder restored from {previous} to PENDING.", actor="db")
    capture_context_event(
        event_type="reminder_restored",
        title=(result.get("description") or id)[:160],
        summary=f"Reminder restored from {previous} to PENDING.",
        body=(result.get("description") or "")[:1600],
        context_key=f"reminder:{id}",
        context_title=(result.get("description") or id)[:160],
        context_summary=(result.get("description") or "")[:600],
        context_type="reminder",
        state="active",
        owner="user",
        actor="db",
        source_type="reminder",
        source_id=id,
        metadata={"previous_status": previous, "status": "PENDING"},
        ttl_hours=24,
    )
    return result


def add_reminder_note(id: str, note: str, actor: str = "nexo") -> dict:
    """Append an operational note to a reminder history."""
    row = get_reminder(id)
    if not row:
        return {"error": f"Reminder {id} not found"}
    history = add_item_history("reminder", id, "note", note=note, actor=actor)
    capture_context_event(
        event_type="reminder_note",
        title=(row.get("description") or id)[:160],
        summary=note[:600],
        body=note[:1600],
        context_key=f"reminder:{id}",
        context_title=(row.get("description") or id)[:160],
        context_summary=(row.get("description") or "")[:600],
        context_type="reminder",
        state=_context_state_from_status(row.get("status")),
        owner="user",
        actor=actor,
        source_type="reminder",
        source_id=id,
        metadata={"status": row.get("status", "")},
        ttl_hours=24,
    )
    return history


def get_reminders(filter_type: str = "all") -> list[dict]:
    """Get reminders by filter: active, due, completed, deleted, history."""
    conn = get_db()
    today = datetime.date.today().isoformat()
    if filter_type == "completed":
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status LIKE 'COMPLETED%' ORDER BY updated_at DESC"
        ).fetchall()
    elif filter_type == "deleted":
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status = 'DELETED' ORDER BY updated_at DESC"
        ).fetchall()
    elif filter_type in {"history", "any"}:
        rows = conn.execute(
            "SELECT * FROM reminders ORDER BY updated_at DESC"
        ).fetchall()
    elif filter_type == "due":
        rows = conn.execute(
            f"SELECT * FROM reminders WHERE {_active_status_where()} "
            "AND date IS NOT NULL AND date <= ? "
            "ORDER BY date ASC",
            (today,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM reminders WHERE {_active_status_where()} "
            "ORDER BY date ASC NULLS LAST"
        ).fetchall()
    return [dict(r) for r in rows]


def get_reminder(id: str, include_history: bool = False) -> dict | None:
    """Get a single reminder by id, optionally with history and read token."""
    conn = get_db()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    if include_history:
        result["history"] = get_item_history("reminder", id)
        result["history_rules"] = _history_rules("reminder")
        result["read_token"] = _issue_item_read_token("reminder", id)
    return result


def get_reminder_history(id: str, limit: int = 20) -> list[dict]:
    return get_item_history("reminder", id, limit=limit)


def find_similar_followups(description: str, threshold: float = 0.3) -> list[dict]:
    """Find open followups similar to a description using keyword overlap."""
    conn = get_db()
    rows = conn.execute(
        f"SELECT * FROM followups WHERE {_active_status_where()}"
    ).fetchall()

    def tokenize(text: str) -> set[str]:
        return {w.lower() for w in text.split() if len(w) > 3}

    query_tokens = tokenize(description)
    if not query_tokens:
        return []

    matches = []
    for row in rows:
        existing_tokens = tokenize(f"{row['id']} {row['description']} {row['verification'] or ''}")
        if not existing_tokens:
            continue
        intersection = query_tokens & existing_tokens
        if not intersection:
            continue
        smaller = min(len(query_tokens), len(existing_tokens))
        score = len(intersection) / smaller if smaller else 0
        if score >= threshold:
            matches.append({**dict(row), "_similarity": round(score, 2)})

    matches.sort(key=lambda x: x["_similarity"], reverse=True)
    return matches[:5]


# ── Followups ──────────────────────────────────────────────────────


def create_followup(
    id: str,
    description: str,
    date: str = None,
    verification: str = "",
    status: str = "PENDING",
    reasoning: str = "",
    recurrence: str = None,
    priority: str = "medium",
    internal: object = None,
    owner: str | None = None,
) -> dict:
    """Create a new followup with optional reasoning and recurrence.

    Agents may override the default classification via `internal` and
    `owner`. Omitted values are filled by classify_task() using the
    legacy heuristics so pre-migration callers keep working identically.
    """
    conn = get_db()
    now = now_epoch()
    similar = find_similar_followups(description)
    warning = ""
    if similar:
        ids = ", ".join(s["id"] for s in similar[:3])
        warning = (
            f" ⚠ SIMILAR FOLLOWUPS EXIST: {ids} "
            f"(scores: {', '.join(str(s['_similarity']) for s in similar[:3])}). Consider updating instead."
        )

    auto_internal, auto_owner = classify_task(id, description, None, recurrence)
    internal_value = normalise_internal(internal)
    if internal_value is None:
        internal_value = auto_internal
    owner_value = normalise_owner(owner) or auto_owner

    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(followups)").fetchall()}
    payload: dict[str, object] = {
        "id": id,
        "date": date,
        "description": description,
        "verification": verification,
        "status": status,
        "reasoning": reasoning,
        "recurrence": recurrence,
        "created_at": now,
        "updated_at": now,
    }
    if "priority" in columns:
        payload["priority"] = priority or "medium"
    if "internal" in columns:
        payload["internal"] = internal_value
    if "owner" in columns:
        payload["owner"] = owner_value

    insert_columns = [column for column in payload if column in columns]
    placeholders = ", ".join("?" for _ in insert_columns)

    try:
        conn.execute(
            f"INSERT INTO followups ({', '.join(insert_columns)}) VALUES ({placeholders})",
            [payload[column] for column in insert_columns],
        )
        conn.commit()
        if _table_exists(conn, "unified_search"):
            fts_upsert("followup", id, id, f"{description} {verification} {reasoning}", "followup", commit=False)
    except sqlite3.IntegrityError:
        return {"error": f"Followup {id} already exists. Use update instead."}

    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    add_item_history(
        "followup",
        id,
        "created",
        note=f"Followup created. Date={date or '—'}. Recurrence={recurrence or '—'}.",
        actor="db",
    )
    capture_context_event(
        event_type="followup_created",
        title=description[:160],
        summary=description[:600],
        body=f"Verification={verification[:240]}. Reasoning={reasoning[:240]}.",
        context_key=f"followup:{id}",
        context_title=description[:160],
        context_summary=description[:600],
        context_type="followup",
        state=_context_state_from_status(status),
        owner="nexo",
        actor="db",
        source_type="followup",
        source_id=id,
        metadata={"status": status, "date": date or "", "priority": priority or "medium"},
        ttl_hours=24,
    )
    result = dict(row)
    if warning:
        result["warning"] = warning
    return result


def update_followup(
    id: str,
    *,
    log_history: bool = True,
    history_event: str = "updated",
    history_actor: str = "db",
    history_note: str = "",
    **kwargs,
) -> dict:
    """Update any fields of a followup."""
    conn = get_db()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    if not row:
        return {"error": f"Followup {id} not found"}

    allowed = {
        "description", "date", "verification", "status",
        "reasoning", "recurrence", "priority", "internal", "owner",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if "internal" in updates:
        coerced = normalise_internal(updates["internal"])
        if coerced is None:
            updates.pop("internal")
        else:
            updates["internal"] = coerced
    if "owner" in updates:
        coerced = normalise_owner(updates["owner"])
        if coerced is None:
            updates.pop("owner")
        else:
            updates["owner"] = coerced
    if not updates:
        return {"error": "No valid fields to update"}

    table_columns = {
        str(r["name"]) for r in conn.execute("PRAGMA table_info(followups)").fetchall()
    }
    updates = {k: v for k, v in updates.items() if k in table_columns or k == "updated_at"}

    updates["updated_at"] = now_epoch()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE followups SET {set_clause} WHERE id = ?", values)
    conn.commit()

    new_row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    current = dict(new_row) if new_row else dict(row)
    if new_row and _table_exists(conn, "unified_search"):
        new_row_dict = dict(new_row)
        fts_upsert(
            "followup",
            id,
            id,
            f"{new_row_dict.get('description','')} {new_row_dict.get('verification','')} {new_row_dict.get('reasoning','')}",
            "followup",
            commit=False,
        )
    if log_history:
        note = history_note or _format_changes(
            row,
            new_row,
            ["description", "date", "verification", "status", "reasoning", "recurrence", "priority"],
        )
        add_item_history("followup", id, history_event, note=note or "Followup updated.", actor=history_actor)
    capture_context_event(
        event_type=f"followup_{history_event}",
        title=(new_row["description"] if new_row else row["description"])[:160],
        summary=((note if log_history else history_note) or "Followup updated.")[:600],
        body=((new_row["description"] if new_row else row["description"]) or "")[:1600],
        context_key=f"followup:{id}",
        context_title=(new_row["description"] if new_row else row["description"])[:160],
        context_summary=((new_row["description"] if new_row else row["description"]) or "")[:600],
        context_type="followup",
        state=_context_state_from_status(current.get("status")),
        owner="nexo",
        actor=history_actor,
        source_type="followup",
        source_id=id,
        metadata={
            "status": current.get("status", ""),
            "date": current.get("date", ""),
            "priority": current.get("priority", ""),
        },
        ttl_hours=24,
    )
    return dict(new_row)


def _calc_next_recurrence_date(recurrence: str, current_date: str = None) -> str | None:
    """Calculate the next date for a recurring followup."""
    today = datetime.date.today()
    base = datetime.date.fromisoformat(current_date) if current_date else today

    if recurrence.startswith("weekly:"):
        day_name = recurrence.split(":")[1].lower()
        day_map = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        target_day = day_map.get(day_name, 0)
        days_ahead = (target_day - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return (today + datetime.timedelta(days=days_ahead)).isoformat()

    if recurrence.startswith("monthly:"):
        target_day = int(recurrence.split(":")[1])
        if today.month == 12:
            year, month = today.year + 1, 1
        else:
            year, month = today.year, today.month + 1
        import calendar

        max_day = calendar.monthrange(year, month)[1]
        return datetime.date(year, month, min(target_day, max_day)).isoformat()

    if recurrence == "quarterly":
        month = base.month + 3
        year = base.year
        if month > 12:
            month -= 12
            year += 1
        import calendar

        max_day = calendar.monthrange(year, month)[1]
        return datetime.date(year, month, min(base.day, max_day)).isoformat()

    return None


def complete_followup(id: str, result: str = "") -> dict:
    """Mark a followup as completed. If recurring, archive old row and spawn next."""
    conn = get_db()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    if not row:
        return {"error": f"Followup {id} not found"}

    kwargs = {"status": "COMPLETED"}
    if result:
        existing = row["verification"] or ""
        kwargs["verification"] = f"{existing}\n{result}".strip() if existing else result

    update_result = update_followup(id, log_history=False, **kwargs)
    if "error" in update_result:
        return update_result
    add_item_history(
        "followup",
        id,
        "completed",
        note=result or "Followup marked COMPLETED.",
        actor="db",
    )
    capture_context_event(
        event_type="followup_completed",
        title=(update_result.get("description") or id)[:160],
        summary=(result or "Followup marked COMPLETED.")[:600],
        body=(update_result.get("description") or "")[:1600],
        context_key=f"followup:{id}",
        context_title=(update_result.get("description") or id)[:160],
        context_summary=(update_result.get("description") or "")[:600],
        context_type="followup",
        state="resolved",
        owner="nexo",
        actor="db",
        source_type="followup",
        source_id=id,
        metadata={"status": "COMPLETED"},
        ttl_hours=24,
    )

    recurrence = row["recurrence"]
    if recurrence:
        today = datetime.date.today().isoformat()
        next_date = _calc_next_recurrence_date(recurrence, row["date"])
        if next_date:
            archived_id = f"{id}-{today}"
            conn.execute("UPDATE followups SET id = ? WHERE id = ?", (archived_id, id))
            _reassign_item_identity(conn, "followup", id, archived_id)
            conn.commit()

            if _table_exists(conn, "unified_search"):
                conn.execute("DELETE FROM unified_search WHERE source = 'followup' AND source_id = ?", (id,))
            archived_row = conn.execute("SELECT * FROM followups WHERE id = ?", (archived_id,)).fetchone()
            if archived_row and _table_exists(conn, "unified_search"):
                fts_upsert(
                    "followup",
                    archived_id,
                    archived_id,
                    f"{archived_row['description']} {archived_row['verification'] or ''} {archived_row['reasoning'] or ''}",
                    "followup",
                    commit=False,
                )

            create_followup(
                id=id,
                description=row["description"],
                date=next_date,
                verification="",
                reasoning=row["reasoning"] or "",
                recurrence=recurrence,
            )
            add_item_history(
                "followup",
                archived_id,
                "recurrence_archived",
                note=f"Recurring followup archived as {archived_id}. Next occurrence spawned as {id} for {next_date}.",
                actor="db",
            )
            add_item_history(
                "followup",
                id,
                "recurrence_spawned",
                note=f"Spawned automatically from {archived_id}.",
                actor="db",
                metadata={"source_followup_id": archived_id},
            )
            capture_context_event(
                event_type="followup_recurrence_archived",
                title=(archived_row["description"] or archived_id)[:160],
                summary=f"Recurring followup archived as {archived_id}. Next occurrence spawned as {id} for {next_date}.",
                body=(archived_row["description"] or "")[:1600],
                context_key=f"followup:{archived_id}",
                context_title=(archived_row["description"] or archived_id)[:160],
                context_summary=(archived_row["description"] or "")[:600],
                context_type="followup",
                state="resolved",
                owner="nexo",
                actor="db",
                source_type="followup",
                source_id=archived_id,
                metadata={"next_id": id, "next_date": next_date},
                ttl_hours=24,
            )
            return {
                "id": archived_id,
                "status": "COMPLETED",
                "recurrence": recurrence,
                "next_id": id,
                "next_date": next_date,
            }

    return update_result


def delete_followup(id: str) -> bool:
    """Soft-delete a followup by setting status to DELETED."""
    result = update_followup(id, status="DELETED", log_history=False)
    if "error" in result:
        return False
    add_item_history("followup", id, "deleted", note="Followup soft-deleted (status=DELETED).", actor="db")
    capture_context_event(
        event_type="followup_deleted",
        title=(result.get("description") or id)[:160],
        summary="Followup soft-deleted (status=DELETED).",
        body=(result.get("description") or "")[:1600],
        context_key=f"followup:{id}",
        context_title=(result.get("description") or id)[:160],
        context_summary=(result.get("description") or "")[:600],
        context_type="followup",
        state="abandoned",
        owner="nexo",
        actor="db",
        source_type="followup",
        source_id=id,
        metadata={"status": "DELETED"},
        ttl_hours=24,
    )
    return True


def restore_followup(id: str) -> dict:
    """Restore a followup from DELETED to PENDING."""
    row = get_followup(id)
    if not row:
        return {"error": f"Followup {id} not found"}
    result = update_followup(id, status="PENDING", log_history=False)
    if "error" in result:
        return result
    previous = row.get("status") or "unknown"
    add_item_history("followup", id, "restored", note=f"Followup restored from {previous} to PENDING.", actor="db")
    capture_context_event(
        event_type="followup_restored",
        title=(result.get("description") or id)[:160],
        summary=f"Followup restored from {previous} to PENDING.",
        body=(result.get("description") or "")[:1600],
        context_key=f"followup:{id}",
        context_title=(result.get("description") or id)[:160],
        context_summary=(result.get("description") or "")[:600],
        context_type="followup",
        state="active",
        owner="nexo",
        actor="db",
        source_type="followup",
        source_id=id,
        metadata={"previous_status": previous, "status": "PENDING"},
        ttl_hours=24,
    )
    return result


def add_followup_note(id: str, note: str, actor: str = "nexo") -> dict:
    """Append an operational note to a followup history."""
    row = get_followup(id)
    if not row:
        return {"error": f"Followup {id} not found"}
    history = add_item_history("followup", id, "note", note=note, actor=actor)
    capture_context_event(
        event_type="followup_note",
        title=(row.get("description") or id)[:160],
        summary=note[:600],
        body=note[:1600],
        context_key=f"followup:{id}",
        context_title=(row.get("description") or id)[:160],
        context_summary=(row.get("description") or "")[:600],
        context_type="followup",
        state=_context_state_from_status(row.get("status")),
        owner="nexo",
        actor=actor,
        source_type="followup",
        source_id=id,
        metadata={"status": row.get("status", "")},
        ttl_hours=24,
    )
    return history


def get_followups(filter_type: str = "all") -> list[dict]:
    """Get followups by filter: active, due, completed, deleted, history."""
    conn = get_db()
    today = datetime.date.today().isoformat()
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(followups)").fetchall()}
    if "impact_score" in columns:
        active_order = (
            "ORDER BY "
            "CASE WHEN COALESCE(impact_score, 0) > 0 THEN 0 ELSE 1 END ASC, "
            "COALESCE(impact_score, 0) DESC, "
            "CASE WHEN date IS NULL OR date = '' THEN 1 ELSE 0 END ASC, "
            "date ASC, updated_at DESC"
        )
    else:
        active_order = "ORDER BY date ASC NULLS LAST"
    if filter_type == "completed":
        rows = conn.execute(
            "SELECT * FROM followups WHERE status LIKE 'COMPLETED%' ORDER BY updated_at DESC"
        ).fetchall()
    elif filter_type == "deleted":
        rows = conn.execute(
            "SELECT * FROM followups WHERE status = 'DELETED' ORDER BY updated_at DESC"
        ).fetchall()
    elif filter_type in {"history", "any"}:
        rows = conn.execute(
            "SELECT * FROM followups ORDER BY updated_at DESC"
        ).fetchall()
    elif filter_type == "due":
        rows = conn.execute(
            f"SELECT * FROM followups WHERE {_active_status_where()} "
            "AND date IS NOT NULL AND date <= ? "
            f"{active_order}",
            (today,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM followups WHERE {_active_status_where()} "
            f"{active_order}"
        ).fetchall()
    return [dict(r) for r in rows]


def get_followup(id: str, include_history: bool = False) -> dict | None:
    """Get a single followup by id, optionally with history and read token."""
    conn = get_db()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    if include_history:
        result["history"] = get_item_history("followup", id)
        result["history_rules"] = _history_rules("followup")
        result["read_token"] = _issue_item_read_token("followup", id)
    return result


def get_followup_history(id: str, limit: int = 20) -> list[dict]:
    return get_item_history("followup", id, limit=limit)


def _parse_date(date_str: str | None) -> datetime.date | None:
    text = str(date_str or "").strip()
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text[:10])
    except (ValueError, TypeError):
        return None


def compute_followup_impact(followup: dict) -> dict:
    """Compute a deterministic impact score for one followup.

    v1 is intentionally simple and transparent:
    - business impact comes from declared priority
    - temporal urgency comes from due date proximity
    - success probability rewards concrete, specified followups
    - cost of inaction grows with priority and overdue state
    """
    priority = str(followup.get("priority") or "medium").strip().lower()
    priority_map = {"critical": 10.0, "high": 8.0, "medium": 5.0, "low": 2.0}
    business_impact = priority_map.get(priority, 5.0)

    due_date = _parse_date(followup.get("date"))
    today = datetime.date.today()
    if due_date is None:
        temporal_urgency = 3.0
        due_label = "undated"
        overdue = False
    else:
        days = (due_date - today).days
        overdue = days <= 0
        if days <= 0:
            temporal_urgency = 10.0
            due_label = "due_or_overdue"
        elif days <= 2:
            temporal_urgency = 8.0
            due_label = "1_2_days"
        elif days <= 7:
            temporal_urgency = 5.0
            due_label = "3_7_days"
        else:
            temporal_urgency = 2.0
            due_label = "later"

    status = str(followup.get("status") or "PENDING").strip().upper()
    verification = str(followup.get("verification") or "").strip()
    reasoning = str(followup.get("reasoning") or "").strip()
    recurrence = str(followup.get("recurrence") or "").strip()

    success_probability = 0.6
    if verification:
        success_probability += 0.15
    if reasoning:
        success_probability += 0.1
    if due_date is not None:
        success_probability += 0.05
    if recurrence:
        success_probability += 0.05
    if status in {"BLOCKED", "WAITING"}:
        success_probability -= 0.35
    success_probability = max(0.2, min(0.95, round(success_probability, 2)))

    cost_of_inaction = business_impact
    if overdue:
        cost_of_inaction += 1.0
    if recurrence:
        cost_of_inaction += 0.5
    cost_of_inaction = min(10.0, round(cost_of_inaction, 2))

    impact_score = round(
        (business_impact * temporal_urgency * success_probability * cost_of_inaction) / 10.0,
        2,
    )
    reasoning_bits = [
        f"priority={priority}",
        f"due={due_label}",
        f"verification={'yes' if verification else 'no'}",
        f"reasoning={'yes' if reasoning else 'no'}",
    ]
    if recurrence:
        reasoning_bits.append("recurring=yes")
    if status in {"BLOCKED", "WAITING"}:
        reasoning_bits.append(f"status={status.lower()}")
    factors = {
        "business_impact": business_impact,
        "temporal_urgency": temporal_urgency,
        "success_probability": success_probability,
        "cost_of_inaction": cost_of_inaction,
        "reasoning": "; ".join(reasoning_bits),
    }
    return {
        "impact_score": impact_score,
        "factors": factors,
        "reasoning": factors["reasoning"],
    }


def score_followup(id: str) -> dict:
    """Compute and persist impact scoring for a single followup."""
    conn = get_db()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    if not row:
        return {"error": f"Followup {id} not found"}
    computed = compute_followup_impact(dict(row))
    factors_json = json.dumps(computed["factors"], ensure_ascii=False, sort_keys=True)
    conn.execute(
        "UPDATE followups SET impact_score = ?, impact_factors = ?, last_scored_at = datetime('now'), updated_at = updated_at WHERE id = ?",
        (computed["impact_score"], factors_json, id),
    )
    conn.commit()
    refreshed = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    result = dict(refreshed)
    result["impact_factors"] = computed["factors"]
    result["impact_reasoning"] = computed["reasoning"]
    return result


def score_active_followups(limit: int = 200) -> list[dict]:
    """Score all active followups and return them ordered by the persisted result."""
    conn = get_db()
    rows = conn.execute(
        f"""SELECT * FROM followups
            WHERE {_active_status_where()}
            ORDER BY
              CASE WHEN date IS NULL OR date = '' THEN 1 ELSE 0 END ASC,
              date ASC,
              created_at ASC
            LIMIT ?""",
        (max(1, int(limit)),),
    ).fetchall()
    scored = [score_followup(str(row["id"])) for row in rows]
    return sorted(scored, key=lambda row: (-(float(row.get("impact_score") or 0)), row.get("date") or "9999-12-31"))
