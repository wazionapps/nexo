"""NEXO DB — Reminders and Followups CRUD."""
import sqlite3
import time
from datetime import datetime, timedelta

def _get_db():
    from db import get_db
    return get_db()


def __now_epoch():
    return time.time()


def __fts_upsert(*args, **kwargs):
    from db import fts_upsert
    return _fts_upsert(*args, **kwargs)


# ── Reminders ──────────────────────────────────────────────────────

def create_reminder(id: str, description: str, date: str = None,
                    status: str = 'PENDIENTE', category: str = 'general') -> dict:
    """Create a new reminder."""
    conn = _get_db()
    now = _now_epoch()
    try:
        conn.execute(
            "INSERT INTO reminders (id, date, description, status, category, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id, date, description, status, category, now, now)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return {"error": f"Reminder {id} already exists. Use update instead."}
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    return dict(row)


def update_reminder(id: str, **kwargs) -> dict:
    """Update any fields of a reminder: description, date, status, category."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    if not row:
            return {"error": f"Reminder {id} not found"}
    allowed = {"description", "date", "status", "category"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
            return {"error": "No valid fields to update"}
    updates["updated_at"] = _now_epoch()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE reminders SET {set_clause} WHERE id = ?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    return dict(row)


def complete_reminder(id: str) -> dict:
    """Mark a reminder as completed with today's date."""
    today = datetime.date.today().isoformat()
    return update_reminder(id, status="COMPLETADO")


def delete_reminder(id: str) -> bool:
    """Delete a reminder."""
    conn = _get_db()
    result = conn.execute("DELETE FROM reminders WHERE id = ?", (id,))
    conn.commit()
    deleted = result.rowcount > 0
    return deleted


def get_reminders(filter_type: str = 'all') -> list[dict]:
    """Get reminders by filter: 'all' (active), 'due' (date <= today), 'completed'."""
    conn = _get_db()
    today = datetime.date.today().isoformat()
    if filter_type == 'completed':
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status LIKE 'COMPLETADO%' ORDER BY updated_at DESC"
        ).fetchall()
    elif filter_type == 'due':
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status NOT LIKE 'COMPLETADO%' "
            "AND status != 'ELIMINADO' AND date IS NOT NULL AND date <= ? "
            "ORDER BY date ASC",
            (today,)
        ).fetchall()
    else:  # 'all' — active only
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status NOT LIKE 'COMPLETADO%' "
            "AND status != 'ELIMINADO' ORDER BY date ASC NULLS LAST"
        ).fetchall()
    return [dict(r) for r in rows]


def get_reminder(id: str) -> dict | None:
    """Get a single reminder by id."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    return dict(row) if row else None


# ── Followups ──────────────────────────────────────────────────────

def create_followup(id: str, description: str, date: str = None,
                    verification: str = '', status: str = 'PENDIENTE',
                    reasoning: str = '', recurrence: str = None) -> dict:
    """Create a new followup with optional reasoning and recurrence.

    recurrence format: 'weekly:monday', 'monthly:1', 'monthly:10', 'quarterly', etc.
    When a recurring followup is completed, a new one is auto-created with the next date.
    """
    conn = _get_db()
    now = _now_epoch()
    try:
        conn.execute(
            "INSERT INTO followups (id, date, description, verification, status, reasoning, recurrence, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id, date, description, verification, status, reasoning, recurrence, now, now)
        )
        conn.commit()
        _fts_upsert("followup", id, id, f"{description} {verification} {reasoning}", "followup", commit=False)
    except sqlite3.IntegrityError:
        return {"error": f"Followup {id} already exists. Use update instead."}
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    return dict(row)


def update_followup(id: str, **kwargs) -> dict:
    """Update any fields of a followup: description, date, verification, status, reasoning."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    if not row:
            return {"error": f"Followup {id} not found"}
    allowed = {"description", "date", "verification", "status", "reasoning", "recurrence"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
            return {"error": "No valid fields to update"}
    updates["updated_at"] = _now_epoch()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE followups SET {set_clause} WHERE id = ?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    r = dict(row)
    _fts_upsert("followup", id, id, f"{r.get('description','')} {r.get('verification','')} {r.get('reasoning','')}", "followup", commit=False)
    return r


def _calc_next_recurrence_date(recurrence: str, current_date: str = None) -> str:
    """Calculate the next date for a recurring followup.

    Formats:
        weekly:monday, weekly:thursday, weekly:friday, weekly:sunday
        monthly:1, monthly:10, monthly:15
        quarterly
    """
    today = datetime.date.today()
    base = datetime.date.fromisoformat(current_date) if current_date else today

    if recurrence.startswith('weekly:'):
        day_name = recurrence.split(':')[1].lower()
        day_map = {'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
                   'friday': 4, 'saturday': 5, 'sunday': 6}
        target_day = day_map.get(day_name, 0)
        days_ahead = (target_day - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7  # next week, not today
        return (today + datetime.timedelta(days=days_ahead)).isoformat()

    elif recurrence.startswith('monthly:'):
        target_day = int(recurrence.split(':')[1])
        # Next month from today
        if today.month == 12:
            next_date = datetime.date(today.year + 1, 1, min(target_day, 28))
        else:
            import calendar
            max_day = calendar.monthrange(today.year, today.month + 1)[1]
            next_date = datetime.date(today.year, today.month + 1, min(target_day, max_day))
        return next_date.isoformat()

    elif recurrence == 'quarterly':
        # 3 months from current date
        month = base.month + 3
        year = base.year
        if month > 12:
            month -= 12
            year += 1
        import calendar
        max_day = calendar.monthrange(year, month)[1]
        return datetime.date(year, month, min(base.day, max_day)).isoformat()

    return None


def complete_followup(id: str, result: str = '') -> dict:
    """Mark a followup as completed with today's date and optional result.
    If the followup has a recurrence pattern, auto-creates the next occurrence."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    if not row:
        return {"error": f"Followup {id} not found"}

    today = datetime.date.today().isoformat()
    kwargs = {"status": "COMPLETADO"}
    if result:
        existing = row["verification"] or ''
        kwargs["verification"] = f"{existing}\n{result}".strip() if existing else result

    update_result = update_followup(id, **kwargs)

    # Auto-regenerate if recurring
    recurrence = row["recurrence"]
    if recurrence:
        next_date = _calc_next_recurrence_date(recurrence, row["date"])
        if next_date:
            # Rename completed one to include date suffix, then create fresh one
            archived_id = f"{id}-{today}"
            conn.execute("UPDATE followups SET id = ? WHERE id = ?", (archived_id, id))
            conn.commit()
            create_followup(
                id=id,
                description=row["description"],
                date=next_date,
                verification='',
                reasoning=row["reasoning"] or '',
                recurrence=recurrence,
            )

    return update_result


def delete_followup(id: str) -> bool:
    """Delete a followup."""
    conn = _get_db()
    result = conn.execute("DELETE FROM followups WHERE id = ?", (id,))
    conn.execute("DELETE FROM unified_search WHERE source = 'followup' AND source_id = ?", (str(id),))
    conn.commit()
    deleted = result.rowcount > 0
    return deleted


def get_followups(filter_type: str = 'all') -> list[dict]:
    """Get followups by filter: 'all' (active), 'due' (date <= today), 'completed'."""
    conn = _get_db()
    today = datetime.date.today().isoformat()
    if filter_type == 'completed':
        rows = conn.execute(
            "SELECT * FROM followups WHERE status LIKE 'COMPLETADO%' ORDER BY updated_at DESC"
        ).fetchall()
    elif filter_type == 'due':
        rows = conn.execute(
            "SELECT * FROM followups WHERE status NOT LIKE 'COMPLETADO%' "
            "AND status != 'ELIMINADO' AND date IS NOT NULL AND date <= ? "
            "ORDER BY date ASC",
            (today,)
        ).fetchall()
    else:  # 'all' — active only
        rows = conn.execute(
            "SELECT * FROM followups WHERE status NOT LIKE 'COMPLETADO%' "
            "AND status != 'ELIMINADO' ORDER BY date ASC NULLS LAST"
        ).fetchall()
    return [dict(r) for r in rows]


def get_followup(id: str) -> dict | None:
    """Get a single followup by id."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    return dict(row) if row else None
