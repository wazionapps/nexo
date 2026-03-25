"""CRUD handlers for reminders and followups — operates on SQLite via db.py."""

from db import (
    create_reminder, update_reminder, complete_reminder, delete_reminder,
    get_reminders, get_reminder,
    create_followup, update_followup, complete_followup, delete_followup,
    get_followups, get_followup,
    find_decisions_by_context_ref, update_decision_outcome,
)


# ── Reminders ──────────────────────────────────────────────────────────────────

def handle_reminder_create(id: str, description: str, date: str = '', category: str = 'general') -> str:
    """Create a new reminder. id must start with 'R'."""
    if not id.startswith('R'):
        return f"ERROR: El ID del recordatorio debe empezar por 'R' (recibido: '{id}')."

    result = create_reminder(id=id, description=description, date=date or None, category=category)
    if not result or "error" in result:
        error_msg = result.get("error", "desconocido") if isinstance(result, dict) else "desconocido"
        return f"ERROR: {error_msg}"

    fecha_str = date if date else 'no date'
    return f"Reminder {id} created. Date: {fecha_str}. Category: {category}."


def handle_reminder_update(id: str, description: str = '', date: str = '', status: str = '', category: str = '') -> str:
    """Update one or more fields of an existing reminder."""
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
    if not result:
        return f"ERROR: Reminder {id} not found."

    changed = ', '.join(fields.keys())
    return f"Reminder {id} updated: {changed}."


def handle_reminder_complete(id: str) -> str:
    """Mark a reminder as completed."""
    result = complete_reminder(id=id)
    if not result or "error" in result:
        return f"ERROR: Reminder {id} not found."

    return f"Reminder {id} marked COMPLETED."


def handle_reminder_delete(id: str) -> str:
    """Delete a reminder permanently."""
    result = delete_reminder(id=id)
    if not result:
        return f"ERROR: Reminder {id} not found."

    return f"Reminder {id} deleted."


# ── Followups ──────────────────────────────────────────────────────────────────

def handle_followup_create(id: str, description: str, date: str = '', verification: str = '', reasoning: str = '', recurrence: str = '') -> str:
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

    result = create_followup(id=id, description=description, date=date or None, verification=verification, reasoning=reasoning, recurrence=recurrence or None)
    if not result or "error" in result:
        error_msg = result.get("error", "desconocido") if isinstance(result, dict) else "desconocido"
        return f"ERROR: {error_msg}"

    fecha_str = date if date else 'no date'
    rec_str = f" Recurrence: {recurrence}." if recurrence else ""
    return f"Followup {id} created. Date: {fecha_str}.{rec_str}"


def handle_followup_update(id: str, description: str = '', date: str = '', verification: str = '', status: str = '') -> str:
    """Update one or more fields of an existing followup."""
    fields: dict = {}
    if description:
        fields['description'] = description
    if date:
        fields['date'] = date
    if verification:
        fields['verification'] = verification
    if status:
        fields['status'] = status

    if not fields:
        return f"ERROR: No fields specified to update for {id}."

    result = update_followup(id=id, **fields)
    if not result:
        return f"ERROR: Followup {id} not found."

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

    # Auto-link: find decisions whose context_ref matches this followup ID
    msg = f"Followup {id} marked COMPLETED."
    if has_recurrence:
        # The new one was auto-created by complete_followup
        new_row = conn.execute("SELECT date FROM followups WHERE id = ?", (id,)).fetchone()
        if new_row:
            msg += f" ♻️ Siguiente auto-creado para {new_row['date']}."
    linked_decisions = find_decisions_by_context_ref(id)
    if linked_decisions:
        outcome_text = result if result else f"Followup {id} completado"
        for dec in linked_decisions:
            update_decision_outcome(dec['id'], outcome_text)
        dec_ids = ', '.join(f"#{d['id']}" for d in linked_decisions)
        msg += f" Decision(s) {dec_ids} updated with automatic outcome."

    return msg


def handle_followup_delete(id: str) -> str:
    """Delete a followup permanently."""
    result = delete_followup(id=id)
    if not result:
        return f"ERROR: Followup {id} not found."

    return f"Followup {id} deleted."
