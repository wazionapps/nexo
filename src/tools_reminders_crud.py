"""CRUD handlers for reminders and followups — operates on SQLite via db.py."""

from db import (
    create_reminder, update_reminder, complete_reminder, delete_reminder,
    restore_reminder, add_reminder_note, get_reminder,
    create_followup, update_followup, complete_followup, delete_followup,
    restore_followup, add_followup_note, get_followup,
    validate_item_read_token,
    find_decisions_by_context_ref, update_decision_outcome,
)


def _require_item_read(item_type: str, item_id: str, read_token: str) -> str | None:
    ok, message = validate_item_read_token(read_token, item_type, item_id)
    if ok:
        return None
    prefix = "followup" if item_type == "followup" else "reminder"
    return f"ERROR: {message} Use nexo_{prefix}_get(id='{item_id}') first."


def _history_lines(history: list[dict]) -> list[str]:
    if not history:
        return ["- (no history)"]
    lines: list[str] = []
    for event in history:
        created_at = event.get("created_at") or "?"
        event_type = event.get("event_type") or "event"
        actor = event.get("actor") or "system"
        note = (event.get("note") or "").strip()
        suffix = f" — {note}" if note else ""
        lines.append(f"- {created_at} [{event_type}] ({actor}){suffix}")
    return lines


def _format_reminder_payload(reminder: dict) -> str:
    lines = [
        f"REMINDER {reminder['id']}",
        f"Description: {reminder.get('description') or ''}",
        f"Date: {reminder.get('date') or '—'}",
        f"Status: {reminder.get('status') or '—'}",
        f"Category: {reminder.get('category') or 'general'}",
    ]
    history_rules = reminder.get("history_rules") or []
    if history_rules:
        lines.append("Usage rules:")
        lines.extend(f"- {rule}" for rule in history_rules)
    lines.append("History:")
    lines.extend(_history_lines(reminder.get("history") or []))
    if reminder.get("read_token"):
        lines.append(f"READ_TOKEN: {reminder['read_token']}")
    return "\n".join(lines)


def _format_followup_payload(followup: dict) -> str:
    lines = [
        f"FOLLOWUP {followup['id']}",
        f"Description: {followup.get('description') or ''}",
        f"Date: {followup.get('date') or '—'}",
        f"Status: {followup.get('status') or '—'}",
        f"Verification: {followup.get('verification') or '—'}",
        f"Reasoning: {followup.get('reasoning') or '—'}",
        f"Recurrence: {followup.get('recurrence') or '—'}",
        f"Priority: {followup.get('priority') or 'medium'}",
    ]
    history_rules = followup.get("history_rules") or []
    if history_rules:
        lines.append("Usage rules:")
        lines.extend(f"- {rule}" for rule in history_rules)
    lines.append("History:")
    lines.extend(_history_lines(followup.get("history") or []))
    if followup.get("read_token"):
        lines.append(f"READ_TOKEN: {followup['read_token']}")
    return "\n".join(lines)


# ── Reminders ──────────────────────────────────────────────────────────────────

def handle_reminder_create(id: str, description: str, date: str = '', category: str = 'general') -> str:
    """Create a new reminder. id must start with 'R'."""
    if not id.startswith('R'):
        return f"ERROR: Reminder ID must start with 'R' (received: '{id}')."

    result = create_reminder(id=id, description=description, date=date or None, category=category)
    if not result or "error" in result:
        error_msg = result.get("error", "unknown") if isinstance(result, dict) else "unknown"
        return f"ERROR: {error_msg}"

    date_str = date if date else 'no date'
    return f"Reminder created. Date: {date_str}. Category: {category}."


def handle_reminder_get(id: str) -> str:
    """Read a reminder with history and return a read token for safe mutations."""
    result = get_reminder(id=id, include_history=True)
    if not result:
        return f"ERROR: Reminder {id} not found."
    return _format_reminder_payload(result)


def handle_reminder_update(
    id: str,
    description: str = '',
    date: str = '',
    status: str = '',
    category: str = '',
    read_token: str = '',
) -> str:
    """Update one or more fields of an existing reminder."""
    error = _require_item_read("reminder", id, read_token)
    if error:
        return error

    fields: dict = {}
    if description:
        fields['description'] = description
    if date:
        fields['date'] = date
    if status:
        fields['status'] = status
    if category:
        fields['category'] = category

    if not fields:
        return f"ERROR: No fields specified to update for {id}."

    result = update_reminder(id=id, **fields)
    if not result or "error" in result:
        error_msg = result.get("error", f"Reminder {id} not found.") if isinstance(result, dict) else f"Reminder {id} not found."
        return f"ERROR: {error_msg}"

    changed = ', '.join(fields.keys())
    return f"Reminder {id} updated: {changed}."


def handle_reminder_complete(id: str) -> str:
    """Mark a reminder as completed."""
    result = complete_reminder(id=id)
    if not result or "error" in result:
        return f"ERROR: Reminder {id} not found."

    return f"Reminder {id} marked COMPLETED."


def handle_reminder_note(id: str, note: str, read_token: str = '', actor: str = 'nexo') -> str:
    """Append a note to reminder history."""
    if not note.strip():
        return "ERROR: note is required."
    error = _require_item_read("reminder", id, read_token)
    if error:
        return error
    result = add_reminder_note(id=id, note=note.strip(), actor=actor or "nexo")
    if not result or "error" in result:
        error_msg = result.get("error", f"Reminder {id} not found.") if isinstance(result, dict) else f"Reminder {id} not found."
        return f"ERROR: {error_msg}"
    return f"Reminder {id} note added."


def handle_reminder_restore(id: str, read_token: str = '') -> str:
    """Restore a soft-deleted reminder."""
    error = _require_item_read("reminder", id, read_token)
    if error:
        return error
    result = restore_reminder(id=id)
    if not result or "error" in result:
        error_msg = result.get("error", f"Reminder {id} not found.") if isinstance(result, dict) else f"Reminder {id} not found."
        return f"ERROR: {error_msg}"
    return f"Reminder {id} restored to PENDING."


def handle_reminder_delete(id: str, read_token: str = '') -> str:
    """Soft-delete a reminder."""
    error = _require_item_read("reminder", id, read_token)
    if error:
        return error
    result = delete_reminder(id=id)
    if not result:
        return f"ERROR: Reminder {id} not found."

    return f"Reminder {id} soft-deleted."


# ── Followups ──────────────────────────────────────────────────────────────────

def handle_followup_create(
    id: str,
    description: str,
    date: str = '',
    verification: str = '',
    reasoning: str = '',
    recurrence: str = '',
    priority: str = 'medium',
) -> str:
    """Create a new NEXO followup. id must start with 'NF'.

    Args:
        id: Unique ID starting with 'NF'
        description: What to verify/do
        date: Target date YYYY-MM-DD (optional)
        verification: How to verify completion (optional)
        reasoning: WHY this followup exists — what decision/context led to it
        recurrence: Recurrence pattern (optional). Formats: 'weekly:monday', 'monthly:1', 'quarterly'.
                    When completed, auto-creates the next occurrence.
    """
    if not id.startswith('NF'):
        return f"ERROR: Followup ID must start with 'NF' (received: '{id}')."

    result = create_followup(
        id=id,
        description=description,
        date=date or None,
        verification=verification,
        reasoning=reasoning,
        recurrence=recurrence or None,
        priority=priority or "medium",
    )
    if not result or "error" in result:
        error_msg = result.get("error", "unknown") if isinstance(result, dict) else "unknown"
        return f"ERROR: {error_msg}"

    date_str = date if date else 'no date'
    rec_str = f" Recurrence: {recurrence}." if recurrence else ""
    priority_str = f" Priority: {priority or 'medium'}."
    warning = result.get("warning", "")
    warn_str = f"\n{warning}" if warning else ""
    return f"Followup created. Date: {date_str}.{priority_str}{rec_str}{warn_str}"


def handle_followup_get(id: str) -> str:
    """Read a followup with history and return a read token for safe mutations."""
    result = get_followup(id=id, include_history=True)
    if not result:
        return f"ERROR: Followup {id} not found."
    return _format_followup_payload(result)


def handle_followup_update(
    id: str,
    description: str = '',
    date: str = '',
    verification: str = '',
    status: str = '',
    priority: str = '',
    read_token: str = '',
) -> str:
    """Update one or more fields of an existing followup."""
    error = _require_item_read("followup", id, read_token)
    if error:
        return error

    fields: dict = {}
    if description:
        fields['description'] = description
    if date:
        fields['date'] = date
    if verification:
        fields['verification'] = verification
    if status:
        fields['status'] = status
    if priority:
        fields['priority'] = priority

    if not fields:
        return f"ERROR: No fields specified to update for {id}."

    result = update_followup(id=id, **fields)
    if not result or "error" in result:
        error_msg = result.get("error", f"Followup {id} not found.") if isinstance(result, dict) else f"Followup {id} not found."
        return f"ERROR: {error_msg}"

    changed = ', '.join(fields.keys())
    return f"Followup {id} updated: {changed}."


def handle_followup_complete(id: str, result: str = '') -> str:
    """Mark a followup as completed, optionally recording the result.
    Also auto-updates any decision that references this followup in context_ref.
    If the followup is recurring, auto-creates the next occurrence."""
    from db import get_db
    # Check recurrence before completing (complete may rename the ID)
    conn = get_db()
    row = conn.execute("SELECT recurrence FROM followups WHERE id = ?", (id,)).fetchone()
    has_recurrence = row and row["recurrence"]

    db_result = complete_followup(id=id, result=result)
    if not db_result or "error" in db_result:
        return f"ERROR: Followup {id} not found."

    # Emit trust event: task completed successfully
    try:
        from cognitive._trust import adjust_trust
        adjust_trust("task_completed", f"Followup {id} completed")
    except Exception:
        pass

    # Auto-link: find decisions whose context_ref matches this followup ID
    msg = f"Followup {id} marked COMPLETED."
    if has_recurrence:
        # The new one was auto-created by complete_followup
        new_row = conn.execute("SELECT date FROM followups WHERE id = ?", (id,)).fetchone()
        if new_row:
            msg += f" ♻️ Next auto-created for {new_row['date']}."
    linked_decisions = find_decisions_by_context_ref(id)
    if linked_decisions:
        outcome_text = result if result else f"Followup {id} completed"
        for dec in linked_decisions:
            update_decision_outcome(dec['id'], outcome_text)
        dec_ids = ', '.join(f"#{d['id']}" for d in linked_decisions)
        msg += f" Decision(s) {dec_ids} updated with automatic outcome."

    return msg


def handle_followup_note(id: str, note: str, read_token: str = '', actor: str = 'nexo') -> str:
    """Append a note to followup history."""
    if not note.strip():
        return "ERROR: note is required."
    error = _require_item_read("followup", id, read_token)
    if error:
        return error
    result = add_followup_note(id=id, note=note.strip(), actor=actor or "nexo")
    if not result or "error" in result:
        error_msg = result.get("error", f"Followup {id} not found.") if isinstance(result, dict) else f"Followup {id} not found."
        return f"ERROR: {error_msg}"
    return f"Followup {id} note added."


def handle_followup_restore(id: str, read_token: str = '') -> str:
    """Restore a soft-deleted followup."""
    error = _require_item_read("followup", id, read_token)
    if error:
        return error
    result = restore_followup(id=id)
    if not result or "error" in result:
        error_msg = result.get("error", f"Followup {id} not found.") if isinstance(result, dict) else f"Followup {id} not found."
        return f"ERROR: {error_msg}"
    return f"Followup {id} restored to PENDING."


def handle_followup_delete(id: str, read_token: str = '') -> str:
    """Soft-delete a followup."""
    error = _require_item_read("followup", id, read_token)
    if error:
        return error
    result = delete_followup(id=id)
    if not result:
        return f"ERROR: Followup {id} not found."

    return f"Followup {id} soft-deleted."
