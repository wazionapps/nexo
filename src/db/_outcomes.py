from __future__ import annotations
"""NEXO DB — Outcome tracker v1."""

import datetime
import json
from typing import Any

from db._core import get_db

VALID_METRIC_SOURCES = {
    "manual",
    "followup_status",
    "decision_outcome",
    "protocol_task_status",
    "nexo_sqlite",
}
VALID_TARGET_OPERATORS = {"gte", "lte", "eq"}


def _utcnow_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _normalize_deadline(deadline: str = "", *, default_days: int = 7) -> str:
    clean = (deadline or "").strip()
    if clean:
        return clean
    return (datetime.datetime.now() + datetime.timedelta(days=default_days)).isoformat(timespec="seconds")


def _normalize_source(metric_source: str) -> str:
    clean = (metric_source or "manual").strip().lower()
    if clean not in VALID_METRIC_SOURCES:
        raise ValueError(f"metric_source must be one of: {', '.join(sorted(VALID_METRIC_SOURCES))}")
    return clean


def _normalize_operator(target_operator: str) -> str:
    clean = (target_operator or "gte").strip().lower()
    if clean not in VALID_TARGET_OPERATORS:
        raise ValueError(f"target_operator must be one of: {', '.join(sorted(VALID_TARGET_OPERATORS))}")
    return clean


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _append_note(existing: str | None, extra: str) -> str:
    extra = (extra or "").strip()
    if not extra:
        return (existing or "").strip()
    existing = (existing or "").strip()
    if not existing:
        return extra
    if extra in existing:
        return existing
    return f"{existing}\n{extra}"


def _format_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _compare(actual_value: float, target_value: float, operator: str) -> bool:
    if operator == "gte":
        return actual_value >= target_value
    if operator == "lte":
        return actual_value <= target_value
    return actual_value == target_value


def _get_outcome_row(conn, outcome_id: int):
    return conn.execute("SELECT * FROM outcomes WHERE id = ?", (int(outcome_id),)).fetchone()


def _update_outcome(
    outcome_id: int,
    *,
    status: str | None = None,
    actual_value: float | None = None,
    actual_value_text: str | None = None,
    checked_at: str | None = None,
    notes: str | None = None,
    learning_id: int | None = None,
) -> dict:
    conn = get_db()
    row = _get_outcome_row(conn, outcome_id)
    if not row:
        return {"error": f"Outcome {outcome_id} not found"}

    updates: list[str] = []
    params: list[Any] = []

    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if actual_value is not None:
        updates.append("actual_value = ?")
        params.append(actual_value)
    if actual_value_text is not None:
        updates.append("actual_value_text = ?")
        params.append(actual_value_text.strip())
    if checked_at is not None:
        updates.append("checked_at = ?")
        params.append(checked_at)
    if notes is not None:
        updates.append("notes = ?")
        params.append(notes)
    if learning_id is not None:
        updates.append("learning_id = ?")
        params.append(learning_id)

    updates.append("updated_at = datetime('now')")
    params.append(int(outcome_id))
    conn.execute(f"UPDATE outcomes SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    row = _get_outcome_row(conn, outcome_id)
    return dict(row) if row else {"error": f"Outcome {outcome_id} not found after update"}


def create_outcome(
    action_type: str,
    description: str,
    expected_result: str,
    *,
    metric_source: str = "manual",
    metric_query: str = "",
    baseline_value: float | None = None,
    target_value: float | None = None,
    target_operator: str = "gte",
    deadline: str = "",
    action_id: str = "",
    session_id: str = "",
    notes: str = "",
) -> dict:
    conn = get_db()
    clean_action_type = (action_type or "").strip()
    clean_description = (description or "").strip()
    clean_expected = (expected_result or "").strip()
    if not clean_action_type:
        return {"error": "action_type is required"}
    if not clean_description:
        return {"error": "description is required"}
    if not clean_expected:
        return {"error": "expected_result is required"}

    try:
        clean_source = _normalize_source(metric_source)
        clean_operator = _normalize_operator(target_operator)
    except ValueError as exc:
        return {"error": str(exc)}

    clean_query = (metric_query or "").strip()
    if clean_source in {"followup_status", "decision_outcome", "protocol_task_status"} and not (action_id or "").strip():
        return {"error": f"action_id is required for metric_source='{clean_source}'"}
    if clean_source == "nexo_sqlite":
        if not clean_query:
            return {"error": "metric_query is required for metric_source='nexo_sqlite'"}
        query_upper = clean_query.upper()
        if ";" in clean_query.rstrip(";"):
            return {"error": "metric_query must be a single SELECT statement"}
        if not query_upper.startswith("SELECT "):
            return {"error": "metric_query must start with SELECT"}
        for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "ATTACH ", "DETACH ", "PRAGMA "):
            if forbidden in query_upper:
                return {"error": "metric_query must be read-only"}

    cursor = conn.execute(
        """INSERT INTO outcomes (
               action_type, action_id, session_id, description, expected_result,
               metric_source, metric_query, baseline_value, target_value,
               target_operator, deadline, notes
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            clean_action_type,
            (action_id or "").strip(),
            (session_id or "").strip(),
            clean_description,
            clean_expected,
            clean_source,
            clean_query,
            baseline_value,
            target_value,
            clean_operator,
            _normalize_deadline(deadline),
            (notes or "").strip(),
        ),
    )
    conn.commit()
    row = _get_outcome_row(conn, cursor.lastrowid)
    return dict(row) if row else {"error": "Outcome insert failed"}


def get_outcome(outcome_id: int) -> dict | None:
    conn = get_db()
    row = _get_outcome_row(conn, outcome_id)
    return dict(row) if row else None


def list_outcomes(status: str = "", action_type: str = "", limit: int = 50) -> list[dict]:
    conn = get_db()
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append((status or "").strip().lower())
    if action_type:
        clauses.append("action_type = ?")
        params.append((action_type or "").strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""SELECT * FROM outcomes
            {where}
            ORDER BY
              CASE status
                WHEN 'pending' THEN 0
                WHEN 'missed' THEN 1
                WHEN 'met' THEN 2
                ELSE 3
              END,
              deadline ASC,
              created_at DESC
            LIMIT ?""",
        params + [max(1, int(limit))],
    ).fetchall()
    return [dict(row) for row in rows]


def pending_outcomes_due(deadline_before: str | None = None, limit: int = 100) -> list[dict]:
    conn = get_db()
    cutoff = deadline_before or _utcnow_iso()
    rows = conn.execute(
        """SELECT * FROM outcomes
           WHERE status = 'pending' AND deadline <= ?
           ORDER BY deadline ASC, id ASC
           LIMIT ?""",
        (cutoff, max(1, int(limit))),
    ).fetchall()
    return [dict(row) for row in rows]


def find_pending_outcomes_by_action(action_type: str, action_id: str, *, metric_source: str = "") -> list[dict]:
    conn = get_db()
    clauses = [
        "status = 'pending'",
        "action_type = ?",
        "action_id = ?",
    ]
    params: list[Any] = [(action_type or "").strip(), (action_id or "").strip()]
    if metric_source:
        clauses.append("metric_source = ?")
        params.append((metric_source or "").strip().lower())
    rows = conn.execute(
        f"SELECT * FROM outcomes WHERE {' AND '.join(clauses)} ORDER BY deadline ASC, id ASC",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def cancel_outcome(outcome_id: int, reason: str = "") -> dict:
    conn = get_db()
    row = _get_outcome_row(conn, outcome_id)
    if not row:
        return {"error": f"Outcome {outcome_id} not found"}
    notes = _append_note(row["notes"], f"Cancelled: {(reason or '').strip() or 'no reason provided'}")
    return _update_outcome(
        int(outcome_id),
        status="cancelled",
        checked_at=_utcnow_iso(),
        notes=notes,
    )


def _create_miss_learning(row: dict, actual_value: float | None, actual_value_text: str, note: str) -> int | None:
    if row.get("learning_id"):
        return int(row["learning_id"])
    from db._learnings import create_learning

    summary_bits = [
        f"Outcome #{row['id']} missed.",
        f"Action type: {row.get('action_type', '')}.",
        f"Action id: {row.get('action_id', '') or 'N/A'}.",
        f"Description: {row.get('description', '')}.",
        f"Expected: {row.get('expected_result', '')}.",
    ]
    if actual_value is not None:
        summary_bits.append(f"Actual numeric value: {actual_value}.")
    if actual_value_text:
        summary_bits.append(f"Actual evidence: {actual_value_text}.")
    if note:
        summary_bits.append(f"Why missed: {note}.")
    learning = create_learning(
        category="outcomes",
        title=f"Outcome missed: {str(row.get('description', ''))[:80]}",
        content=" ".join(summary_bits),
        reasoning=f"Auto-created from missed outcome #{row['id']}.",
        prevention="Review the action, expected result, target, or deadline before repeating the same move.",
        applies_to=f"outcome:{row['id']}",
    )
    return int(learning["id"]) if learning and learning.get("id") else None


def _status_from_protocol_task(task_row: dict | None, *, deadline_passed: bool) -> tuple[str, float | None, str, str]:
    if not task_row:
        if deadline_passed:
            return "missed", None, "protocol task missing", "Linked protocol task not found."
        return "pending", None, "", ""
    status = str(task_row.get("status") or "").strip().lower()
    if status == "done":
        evidence = (task_row.get("outcome_notes") or task_row.get("close_evidence") or task_row.get("goal") or "").strip()
        return "met", 1.0, evidence, "Linked protocol task closed as done."
    if status in {"failed", "cancelled"}:
        evidence = (task_row.get("outcome_notes") or task_row.get("close_evidence") or status).strip()
        return "missed", 0.0, evidence, f"Linked protocol task closed as {status}."
    if deadline_passed:
        return "missed", None, status or "open", f"Deadline passed while linked protocol task remained {status or 'open'}."
    return "pending", None, status, ""


def evaluate_outcome(
    outcome_id: int,
    *,
    actual_value: float | None = None,
    actual_value_text: str = "",
    create_learning_on_miss: bool = True,
) -> dict:
    conn = get_db()
    row = _get_outcome_row(conn, outcome_id)
    if not row:
        return {"error": f"Outcome {outcome_id} not found"}
    row_d = dict(row)
    if row_d.get("status") != "pending":
        row_d["evaluation"] = "skipped_non_pending"
        return row_d

    now_iso = _utcnow_iso()
    deadline_passed = str(row_d.get("deadline") or "") <= now_iso
    source = (row_d.get("metric_source") or "manual").strip().lower()
    target = _as_float(row_d.get("target_value"))
    operator = (row_d.get("target_operator") or "gte").strip().lower()

    status = "pending"
    actual_num = _as_float(actual_value)
    actual_text = (actual_value_text or "").strip()
    note = ""

    if source == "manual":
        if actual_num is not None:
            actual_text = actual_text or _format_scalar(actual_num)
            if target is None:
                status = "met"
                note = "Manual evidence recorded."
            elif _compare(actual_num, target, operator):
                status = "met"
                note = f"Manual check met target via operator '{operator}'."
            elif deadline_passed:
                status = "missed"
                note = f"Manual check below target and deadline passed (actual={actual_num}, target={target}, op={operator})."
            else:
                note = f"Manual check recorded current value (actual={actual_num}, target={target}, op={operator})."
        elif actual_text:
            status = "met"
            note = "Manual textual evidence recorded."
        elif deadline_passed:
            status = "missed"
            note = "Deadline passed with no manual evidence recorded."

    elif source == "followup_status":
        followup = conn.execute(
            "SELECT status, description, verification FROM followups WHERE id = ?",
            (row_d.get("action_id", ""),),
        ).fetchone()
        followup_status = str(followup["status"]) if followup else ""
        if followup and followup_status.upper().startswith("COMPLETED"):
            status = "met"
            actual_num = 1.0
            actual_text = (followup["verification"] or followup["description"] or followup_status or "").strip()
            note = "Linked followup completed."
        elif deadline_passed:
            status = "missed"
            actual_text = actual_text or (followup_status or "missing")
            note = f"Deadline passed while linked followup remained {followup_status or 'missing'}."

    elif source == "decision_outcome":
        decision = conn.execute(
            "SELECT outcome, status, decision FROM decisions WHERE id = ?",
            (row_d.get("action_id", ""),),
        ).fetchone()
        decision_outcome = (decision["outcome"] or "").strip() if decision else ""
        if decision and decision_outcome:
            status = "met"
            actual_num = 1.0
            actual_text = decision_outcome
            note = "Linked decision outcome recorded."
        elif deadline_passed:
            status = "missed"
            actual_text = actual_text or (str(decision["status"]) if decision else "missing")
            note = f"Deadline passed with no linked decision outcome for decision {row_d.get('action_id', '') or 'N/A'}."

    elif source == "protocol_task_status":
        task = conn.execute(
            "SELECT status, goal, close_evidence, outcome_notes FROM protocol_tasks WHERE task_id = ?",
            (row_d.get("action_id", ""),),
        ).fetchone()
        status, actual_num, actual_text, note = _status_from_protocol_task(dict(task) if task else None, deadline_passed=deadline_passed)

    elif source == "nexo_sqlite":
        query = (row_d.get("metric_query") or "").strip()
        if not query:
            return {"error": f"Outcome {outcome_id} has empty metric_query"}
        if ";" in query.rstrip(";") or not query.upper().startswith("SELECT "):
            return {"error": "Outcome metric_query must be a single SELECT statement"}
        fetched = conn.execute(query).fetchone()
        scalar = fetched[0] if fetched else None
        actual_num = _as_float(scalar)
        actual_text = _format_scalar(scalar)
        if actual_num is None:
            if deadline_passed:
                status = "missed"
                note = f"SQLite query did not return a numeric scalar before deadline. Got: {actual_text or 'empty'}."
            else:
                note = f"SQLite query returned non-numeric scalar: {actual_text or 'empty'}."
        elif target is None:
            if actual_num:
                status = "met"
                note = f"SQLite query returned truthy scalar {actual_num}."
            elif deadline_passed:
                status = "missed"
                note = "SQLite query returned falsy scalar and deadline passed."
            else:
                note = f"SQLite query current scalar is {actual_num}."
        elif _compare(actual_num, target, operator):
            status = "met"
            note = f"SQLite scalar met target (actual={actual_num}, target={target}, op={operator})."
        elif deadline_passed:
            status = "missed"
            note = f"SQLite scalar missed target at deadline (actual={actual_num}, target={target}, op={operator})."
        else:
            note = f"SQLite scalar recorded but target not reached yet (actual={actual_num}, target={target}, op={operator})."

    learning_id = row_d.get("learning_id")
    combined_notes = _append_note(row_d.get("notes"), note)
    if status == "missed" and create_learning_on_miss:
        learning_id = _create_miss_learning(row_d, actual_num, actual_text, note)

    updated = _update_outcome(
        int(outcome_id),
        status=status,
        actual_value=actual_num,
        actual_value_text=actual_text,
        checked_at=now_iso,
        notes=combined_notes,
        learning_id=int(learning_id) if learning_id else None,
    )
    if "error" not in updated:
        updated["evaluation"] = status
    return updated


def set_linked_outcomes_met(
    action_type: str,
    action_id: str,
    *,
    metric_source: str = "",
    actual_value: float | None = 1.0,
    actual_value_text: str = "",
    note: str = "",
) -> list[dict]:
    rows = find_pending_outcomes_by_action(action_type, action_id, metric_source=metric_source)
    updated: list[dict] = []
    for row in rows:
        updated.append(
            _update_outcome(
                int(row["id"]),
                status="met",
                actual_value=actual_value,
                actual_value_text=actual_value_text or row.get("actual_value_text", ""),
                checked_at=_utcnow_iso(),
                notes=_append_note(row.get("notes"), note or "Linked action reached success state."),
            )
        )
    return updated
