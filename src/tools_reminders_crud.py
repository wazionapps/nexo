"""CRUD handlers for reminders and followups — operates on SQLite via db.py."""

from db import (
    create_reminder, update_reminder, complete_reminder, delete_reminder,
    restore_reminder, add_reminder_note, get_reminder,
    create_followup, update_followup, complete_followup, delete_followup,
    restore_followup, add_followup_note, get_followup,
    extract_keywords, get_db, get_followups,
    validate_item_read_token,
    find_decisions_by_context_ref, update_decision_outcome,
    set_linked_outcomes_met,
)


# ── R01 (Fase 2 Protocol Enforcer) dedup threshold ────────────────────
# Two followup descriptions are considered duplicate when Jaccard
# similarity over extracted keywords exceeds this threshold. The plan
# (doc 1 R01) specifies >0.80. Jaccard uses the same keyword extractor
# as find_similar_learnings so dedup behaviour is consistent across
# artifact types. Operators can bypass the warning by passing
# force="true" when the duplicate is intentional (e.g. recurring check
# with a different window). Non-strict / lenient mode only warns, never
# blocks.
R01_SIMILARITY_THRESHOLD = 0.80


def _find_recurrence_conflicts(recurrence: str, exclude_id: str = "") -> list[dict]:
    """Fase 2 R08 helper — return active followups with exactly the same
    recurrence pattern string as the candidate.

    The plan doc 1 R08 targets reminder_create recurrence conflicts, but
    the NEXO core schema carries recurrence on FOLLOWUPS (nexo_followup_create)
    not reminders. This helper applies R08 to followups since that is the
    only artifact in the core with a recurrence field. If a future release
    adds recurrence to reminders, the helper is schema-agnostic enough to
    reuse with a different get_rows() call.

    Exact-string match is intentional — timezone-aware calendar-level
    overlap detection is out of scope and would be easy to get wrong.
    Operators who want to distinguish "weekly:monday" from another
    "weekly:monday" schedule can pass force=true.
    """
    needle = (recurrence or "").strip()
    if not needle:
        return []
    rows = get_followups()  # default returns active followups
    hits: list[dict] = []
    for row in rows:
        existing_id = str(row.get("id") or "")
        if exclude_id and existing_id == exclude_id:
            continue
        existing_rec = str(row.get("recurrence") or "").strip()
        if existing_rec and existing_rec == needle:
            hits.append({
                "id": existing_id,
                "recurrence": existing_rec,
                "description": str(row.get("description") or "")[:120],
            })
    return hits[:5]


# ── R04 (Fase 2 Protocol Enforcer) retroactive-complete threshold ────
# Plan doc 1 R04: when the agent executes an action whose description
# matches an active followup at Jaccard >=0.70, suggest auto-completing
# that followup. This is a SUGGESTION surface — the actual decision
# to call nexo_followup_complete belongs to the caller (heartbeat,
# Cortex). The threshold is lower than R01 (0.80) because R04 fires
# retroactively on action descriptions rather than on new-followup
# creation, and more false positives are acceptable in a suggestion
# channel than in a creation block.
R04_RETROACTIVE_THRESHOLD = 0.70


def find_completable_followups(context_text: str, threshold: float = R04_RETROACTIVE_THRESHOLD) -> list[dict]:
    """R04 helper — return followups that the current action may have already
    completed.

    Reuses extract_keywords for Jaccard. Safe to call from the heartbeat
    path because it runs a single indexed query and a keyword pass over a
    typically small set of active followups.

    Output: list[{"id", "description", "similarity"}] sorted desc,
    capped at 5. Empty list means nothing to suggest.
    """
    keywords_new = set(extract_keywords(context_text or ""))
    if not keywords_new:
        return []
    try:
        rows = get_followups()  # default returns active followups
    except Exception:
        return []
    suggestions: list[dict] = []
    for row in rows:
        desc = str(row.get("description") or "")
        keywords_existing = set(extract_keywords(desc))
        if not keywords_existing:
            continue
        overlap = keywords_new & keywords_existing
        union = keywords_new | keywords_existing
        similarity = (len(overlap) / len(union)) if union else 0.0
        if similarity >= threshold:
            suggestions.append({
                "id": str(row.get("id") or ""),
                "description": desc[:160],
                "similarity": round(similarity, 3),
            })
    suggestions.sort(key=lambda x: x["similarity"], reverse=True)
    return suggestions[:5]


def _find_similar_active_followups(description: str, exclude_id: str = "") -> list[tuple[str, float, str]]:
    """Return active followups whose description exceeds the R01 threshold.

    Output: list of (followup_id, similarity_score, existing_description)
    sorted by similarity descending, capped at 5. Empty list means no
    duplicate risk.

    Jaccard similarity over keyword sets. Cheap, deterministic, and
    language-agnostic modulo the extract_keywords stoplist.
    """
    keywords_new = set(extract_keywords(description or ""))
    if not keywords_new:
        return []
    rows = get_followups()  # default returns active followups
    results: list[tuple[str, float, str]] = []
    for row in rows:
        existing_id = str(row.get("id") or "")
        if exclude_id and existing_id == exclude_id:
            continue
        existing_desc = str(row.get("description") or "")
        keywords_existing = set(extract_keywords(existing_desc))
        if not keywords_existing:
            continue
        overlap = keywords_new & keywords_existing
        union = keywords_new | keywords_existing
        similarity = (len(overlap) / len(union)) if union else 0.0
        if similarity >= R01_SIMILARITY_THRESHOLD:
            results.append((existing_id, round(similarity, 3), existing_desc))
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:5]


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
        f"Owner: {reminder.get('owner') or '—'}",
        f"Internal: {1 if reminder.get('internal') else 0}",
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
        f"Owner: {followup.get('owner') or '—'}",
        f"Internal: {1 if followup.get('internal') else 0}",
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

def handle_reminder_create(
    id: str,
    description: str,
    date: str = '',
    category: str = 'general',
    internal: str = '',
    owner: str = '',
) -> str:
    """Create a new reminder. id must start with 'R'."""
    if not id.startswith('R'):
        return f"ERROR: Reminder ID must start with 'R' (received: '{id}')."

    result = create_reminder(
        id=id,
        description=description,
        date=date or None,
        category=category,
        internal=internal if internal != '' else None,
        owner=owner if owner != '' else None,
    )
    if not result or "error" in result:
        error_msg = result.get("error", "unknown") if isinstance(result, dict) else "unknown"
        return f"ERROR: {error_msg}"

    date_str = date if date else 'no date'
    owner_final = result.get('owner') or '—'
    internal_final = 1 if result.get('internal') else 0
    return (
        f"Reminder created. Date: {date_str}. Category: {category}. "
        f"Owner: {owner_final}. Internal: {internal_final}."
    )


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
    internal: str = '',
    owner: str = '',
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
    if internal != '':
        fields['internal'] = internal
    if owner != '':
        fields['owner'] = owner

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
    internal: str = '',
    owner: str = '',
    force: str = '',
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
        internal: '1' / 'true' hides this task from default user views
                  (agent bookkeeping, protocol enforcement, audits).
                  Omit to let Brain classify by ID prefix heuristic.
        owner: 'user' | 'waiting' | 'agent' | 'shared'. Omit to let
               Brain classify by description verbs.
        force: Set to '1' / 'true' to bypass Fase 2 R01 dedup when the
               near-duplicate followup is intentional. Without force,
               any Jaccard similarity >= 0.80 against an active
               followup returns an error listing the matches.
    """
    if not id.startswith('NF'):
        return f"ERROR: Followup ID must start with 'NF' (received: '{id}')."

    # ── R01 (Fase 2 Protocol Enforcer): reject near-duplicate active followups ──
    force_flag = str(force or "").strip().lower() in {"1", "true", "yes", "on"}
    # ── R08 (Fase 2 Protocol Enforcer): recurrence conflict warning ──
    if not force_flag and recurrence:
        conflicts = _find_recurrence_conflicts(recurrence, exclude_id=id)
        if conflicts:
            lines = [
                f"ERROR: Recurrence conflict detected (R08). Pattern '{recurrence}' "
                f"already used by {len(conflicts)} active followup(s):",
            ]
            for c in conflicts:
                lines.append(f"  - {c['id']}: {c['description']}")
            lines.append("Options:")
            lines.append("  - Pick a different recurrence window (e.g. weekly:tuesday vs weekly:monday)")
            lines.append("  - Complete / archive the existing schedule first")
            lines.append("  - Pass force='true' if two identical-cadence followups are intentional")
            return "\n".join(lines)
    if not force_flag:
        similar = _find_similar_active_followups(description, exclude_id=id)
        if similar:
            best_id, best_sim, best_desc = similar[0]
            lines = [
                f"ERROR: Near-duplicate followup detected (R01). Jaccard >= {R01_SIMILARITY_THRESHOLD:.2f}.",
                f"Best match: {best_id} (similarity {best_sim:.2f}) — {best_desc[:120]}",
                "Options:",
                "  - Update the existing followup via nexo_followup_update",
                "  - Rephrase the description to remove overlap",
                "  - Pass force='true' if the duplication is intentional",
            ]
            if len(similar) > 1:
                extras = ", ".join(f"{sid}({sim:.2f})" for sid, sim, _ in similar[1:])
                lines.append(f"Other matches: {extras}")
            return "\n".join(lines)

    result = create_followup(
        id=id,
        description=description,
        date=date or None,
        verification=verification,
        reasoning=reasoning,
        recurrence=recurrence or None,
        priority=priority or "medium",
        internal=internal if internal != '' else None,
        owner=owner if owner != '' else None,
    )
    if not result or "error" in result:
        error_msg = result.get("error", "unknown") if isinstance(result, dict) else "unknown"
        return f"ERROR: {error_msg}"

    date_str = date if date else 'no date'
    rec_str = f" Recurrence: {recurrence}." if recurrence else ""
    priority_str = f" Priority: {priority or 'medium'}."
    owner_final = result.get('owner') or '—'
    internal_final = 1 if result.get('internal') else 0
    class_str = f" Owner: {owner_final}. Internal: {internal_final}."
    warning = result.get("warning", "")
    warn_str = f"\n{warning}" if warning else ""
    return f"Followup created. Date: {date_str}.{priority_str}{rec_str}{class_str}{warn_str}"


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
    internal: str = '',
    owner: str = '',
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
    if internal != '':
        fields['internal'] = internal
    if owner != '':
        fields['owner'] = owner

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

    try:
        linked_outcomes = set_linked_outcomes_met(
            "followup",
            id,
            metric_source="followup_status",
            actual_value=1.0,
            actual_value_text=result if result else f"Followup {id} completed",
            note="Linked followup completed.",
        )
    except Exception:
        linked_outcomes = []
    if linked_outcomes:
        msg += f" Linked outcomes met: {len(linked_outcomes)}."

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
