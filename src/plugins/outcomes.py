"""Outcome tracker plugin — close action -> expected result -> actual result loops."""

from __future__ import annotations

import json

from db import (
    create_outcome,
    get_outcome,
    list_outcomes,
    cancel_outcome,
    evaluate_outcome,
    list_outcome_pattern_candidates,
    capture_outcome_pattern,
)


def _format_outcome_row(row: dict) -> str:
    actual = row.get("actual_value")
    actual_display = (
        str(actual)
        if actual is not None
        else (row.get("actual_value_text") or "—")
    )
    return (
        f"#{row['id']} [{row.get('status','pending')}] "
        f"{row.get('action_type','custom')}:{row.get('action_id','—') or '—'} "
        f"deadline={row.get('deadline','—')} "
        f"expected={row.get('expected_result','')[:80]} "
        f"actual={str(actual_display)[:80]}"
    )


def handle_outcome_register(
    action_type: str,
    description: str,
    expected_result: str,
    metric_source: str = "manual",
    metric_query: str = "",
    baseline: float | None = None,
    target: float | None = None,
    target_op: str = "gte",
    deadline: str = "",
    action_id: str = "",
    session_id: str = "",
    notes: str = "",
) -> str:
    """Register an expected outcome for an action so NEXO can verify it later."""
    result = create_outcome(
        action_type=action_type,
        description=description,
        expected_result=expected_result,
        metric_source=metric_source,
        metric_query=metric_query,
        baseline_value=baseline,
        target_value=target,
        target_operator=target_op,
        deadline=deadline,
        action_id=action_id,
        session_id=session_id,
        notes=notes,
    )
    if "error" in result:
        return f"ERROR: {result['error']}"
    return (
        f"Outcome #{result['id']} registered [{result['status']}]. "
        f"deadline={result['deadline']} metric_source={result['metric_source']}"
    )


def handle_outcome_check(
    id: int,
    actual_value: float | None = None,
    actual_value_text: str = "",
    create_learning_on_miss: bool = True,
) -> str:
    """Check one outcome now and update its status using linked state or supplied evidence."""
    result = evaluate_outcome(
        int(id),
        actual_value=actual_value,
        actual_value_text=actual_value_text,
        create_learning_on_miss=bool(create_learning_on_miss),
    )
    if "error" in result:
        return f"ERROR: {result['error']}"
    notes = result.get("notes") or ""
    learning = f" learning_id={result.get('learning_id')}" if result.get("learning_id") else ""
    return f"{_format_outcome_row(result)}{learning}\nnotes={notes[:300]}"


def handle_outcome_list(status: str = "", action_type: str = "", limit: int = 20) -> str:
    """List outcomes by status and/or action type."""
    rows = list_outcomes(status=status, action_type=action_type, limit=limit)
    if not rows:
        scope = f"status={status or 'any'} action_type={action_type or 'any'}"
        return f"No outcomes found ({scope})."
    header = f"OUTCOMES ({len(rows)})"
    return "\n".join([header] + [f"  {_format_outcome_row(row)}" for row in rows])


def handle_outcome_cancel(id: int, reason: str = "") -> str:
    """Cancel a pending outcome so it no longer blocks reviews or release loops."""
    result = cancel_outcome(int(id), reason=reason)
    if "error" in result:
        return f"ERROR: {result['error']}"
    return f"Outcome #{result['id']} cancelled."


def handle_outcome_pattern_candidates(min_resolved: int = 3, limit: int = 10) -> str:
    """List repeated resolved outcome patterns that are strong enough to become reusable knowledge."""
    candidates = list_outcome_pattern_candidates(min_resolved=min_resolved, limit=limit)
    return json.dumps({"ok": True, "candidates": candidates}, ensure_ascii=False, indent=2)


def handle_outcome_pattern_capture(pattern_key: str, target: str = "learning", category: str = "outcomes") -> str:
    """Materialize one repeated outcome pattern into a reusable artifact (currently a learning)."""
    result = capture_outcome_pattern(pattern_key=pattern_key, target=target, category=category)
    if "error" in result:
        return json.dumps({"ok": False, "error": result["error"]}, ensure_ascii=False, indent=2)
    return json.dumps(result, ensure_ascii=False, indent=2)


TOOLS = [
    (handle_outcome_register, "nexo_outcome_register", "Register an expected action outcome with metric source, deadline, and optional link to decision/followup/task."),
    (handle_outcome_check, "nexo_outcome_check", "Check a tracked outcome now using linked state or supplied evidence; marks pending/met/missed and may create a learning on miss."),
    (handle_outcome_list, "nexo_outcome_list", "List tracked outcomes filtered by status and/or action type."),
    (handle_outcome_cancel, "nexo_outcome_cancel", "Cancel an outcome so it stops blocking pending/missed reviews."),
    (handle_outcome_pattern_candidates, "nexo_outcome_pattern_candidates", "List repeated resolved outcome patterns from cortex-linked decisions that are consistent enough to become reusable knowledge."),
    (handle_outcome_pattern_capture, "nexo_outcome_pattern_capture", "Capture one repeated outcome pattern as a reusable artifact (currently a learning) once the evidence is consistent."),
]
