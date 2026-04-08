from __future__ import annotations
"""NEXO DB — Reminders and followups with history + soft delete."""

import datetime
import json
import secrets
import sqlite3
from typing import Any

from db._core import get_db, now_epoch
from db._fts import fts_upsert

ACTIVE_EXCLUDED_STATUSES = {"DELETED", "archived", "blocked", "waiting"}
READ_TOKEN_TTL_SECONDS = 30 * 60


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
    rows = conn.execute(
        "SELECT * FROM item_history WHERE item_type = ? AND item_id = ? ORDER BY id DESC LIMIT ?",
        (item_type, item_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _issue_item_read_token(item_type: str, item_id: str, ttl_seconds: int = READ_TOKEN_TTL_SECONDS) -> str:
    conn = get_db()
    now = now_epoch()
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


# ── Reminders ──────────────────────────────────────────────────────


def create_reminder(
    id: str,
    description: str,
    date: str = None,
    status: str = "PENDING",
    category: str = "general",
) -> dict:
    """Create a new reminder."""
    conn = get_db()
    now = now_epoch()
    try:
        conn.execute(
            "INSERT INTO reminders (id, date, description, status, category, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id, date, description, status, category, now, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return {"error": f"Reminder {id} already exists. Use update instead."}

    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    add_item_history(
        "reminder",
        id,
        "created",
        note=f"Reminder created. Category={category}. Date={date or '—'}.",
        actor="db",
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

    allowed = {"description", "date", "status", "category"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return {"error": "No valid fields to update"}

    updates["updated_at"] = now_epoch()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE reminders SET {set_clause} WHERE id = ?", values)
    conn.commit()

    new_row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    if log_history:
        note = history_note or _format_changes(row, new_row, ["description", "date", "status", "category"])
        add_item_history("reminder", id, history_event, note=note or "Reminder updated.", actor=history_actor)
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
    return result


def add_reminder_note(id: str, note: str, actor: str = "nexo") -> dict:
    """Append an operational note to a reminder history."""
    row = get_reminder(id)
    if not row:
        return {"error": f"Reminder {id} not found"}
    return add_item_history("reminder", id, "note", note=note, actor=actor)


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
) -> dict:
    """Create a new followup with optional reasoning and recurrence."""
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

    try:
        conn.execute(
            "INSERT INTO followups (id, date, description, verification, status, reasoning, recurrence, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id, date, description, verification, status, reasoning, recurrence, now, now),
        )
        conn.commit()
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

    allowed = {"description", "date", "verification", "status", "reasoning", "recurrence", "priority"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return {"error": "No valid fields to update"}

    updates["updated_at"] = now_epoch()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE followups SET {set_clause} WHERE id = ?", values)
    conn.commit()

    new_row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    if new_row:
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

    recurrence = row["recurrence"]
    if recurrence:
        today = datetime.date.today().isoformat()
        next_date = _calc_next_recurrence_date(recurrence, row["date"])
        if next_date:
            archived_id = f"{id}-{today}"
            conn.execute("UPDATE followups SET id = ? WHERE id = ?", (archived_id, id))
            _reassign_item_identity(conn, "followup", id, archived_id)
            conn.commit()

            conn.execute("DELETE FROM unified_search WHERE source = 'followup' AND source_id = ?", (id,))
            archived_row = conn.execute("SELECT * FROM followups WHERE id = ?", (archived_id,)).fetchone()
            if archived_row:
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
    return result


def add_followup_note(id: str, note: str, actor: str = "nexo") -> dict:
    """Append an operational note to a followup history."""
    row = get_followup(id)
    if not row:
        return {"error": f"Followup {id} not found"}
    return add_item_history("followup", id, "note", note=note, actor=actor)


def get_followups(filter_type: str = "all") -> list[dict]:
    """Get followups by filter: active, due, completed, deleted, history."""
    conn = get_db()
    today = datetime.date.today().isoformat()
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
            "ORDER BY date ASC",
            (today,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM followups WHERE {_active_status_where()} "
            "ORDER BY date ASC NULLS LAST"
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
