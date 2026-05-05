from __future__ import annotations
"""NEXO DB — Protocol discipline runtime."""

import json
import hashlib
import secrets
import time

from db._core import get_db

VALID_TASK_TYPES = {"answer", "analyze", "edit", "execute", "delegate"}
VALID_OUTCOMES = {"open", "done", "partial", "blocked", "failed", "cancelled"}
VALID_CLOSE_OUTCOMES = VALID_OUTCOMES - {"open"}
VALID_DEBT_STATUS = {"open", "forgiven", "resolved"}
VALID_IMPACT_LEVELS = {"medium", "high", "critical"}


def _task_id() -> str:
    return f"PT-{int(time.time())}-{secrets.randbelow(100000)}"


def _as_json(value) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        value = []
    return json.dumps(value, ensure_ascii=False)


def _as_bool(value) -> int:
    return 1 if bool(value) else 0


def _row_to_dict(row):
    return dict(row) if row else None


def _correction_context_hash(session_id: str, text: str) -> str:
    clean = " ".join(str(text or "").strip().split())[:1200]
    digest = hashlib.sha1(f"{session_id.strip()}\0{clean}".encode("utf-8"), usedforsecurity=False)
    return digest.hexdigest()[:20]


def validate_task_type(task_type: str) -> str:
    clean_type = (task_type or "").strip()
    if clean_type not in VALID_TASK_TYPES:
        expected = ", ".join(sorted(VALID_TASK_TYPES))
        raise ValueError(f"Invalid task_type '{clean_type or '<empty>'}'. Expected one of: {expected}.")
    return clean_type


def validate_impact_level(impact_level: str) -> str:
    clean_level = (impact_level or "").strip()
    if clean_level not in VALID_IMPACT_LEVELS:
        expected = ", ".join(sorted(VALID_IMPACT_LEVELS))
        raise ValueError(f"Invalid impact_level '{clean_level or '<empty>'}'. Expected one of: {expected}.")
    return clean_level


def validate_close_outcome(outcome: str) -> str:
    clean_outcome = (outcome or "").strip()
    if clean_outcome not in VALID_CLOSE_OUTCOMES:
        expected = ", ".join(sorted(VALID_CLOSE_OUTCOMES))
        raise ValueError(f"Invalid close outcome '{clean_outcome or '<empty>'}'. Expected one of: {expected}.")
    return clean_outcome


def create_protocol_task(
    session_id: str,
    goal: str,
    *,
    task_type: str = "answer",
    area: str = "",
    project_hint: str = "",
    context_hint: str = "",
    files=None,
    plan=None,
    known_facts=None,
    unknowns=None,
    constraints=None,
    evidence_refs=None,
    verification_step: str = "",
    cortex_mode: str = "",
    cortex_check_id: str = "",
    cortex_blocked_reason: str = "",
    cortex_warnings=None,
    cortex_rules=None,
    opened_with_guard: bool = False,
    opened_with_rules: bool = False,
    guard_has_blocking: bool = False,
    guard_acknowledged: bool = False,
    guard_acknowledged_at: str = "",
    guard_summary: str = "",
    must_verify: bool = False,
    must_change_log: bool = False,
    must_learning_if_corrected: bool = True,
    must_write_diary_on_close: bool = False,
    response_mode: str = "",
    response_confidence: int = 0,
    response_reasons=None,
    response_high_stakes: bool = False,
) -> dict:
    conn = get_db()
    task_id = _task_id()
    clean_type = validate_task_type(task_type)
    conn.execute(
        """INSERT INTO protocol_tasks (
               task_id, session_id, goal, task_type, area, project_hint, context_hint,
               files, plan, known_facts, unknowns, constraints, evidence_refs, verification_step,
               cortex_mode, cortex_check_id, cortex_blocked_reason, cortex_warnings, cortex_rules,
               opened_with_guard, opened_with_rules, guard_has_blocking, guard_acknowledged,
               guard_acknowledged_at, guard_summary,
               must_verify, must_change_log, must_learning_if_corrected, must_write_diary_on_close,
               response_mode, response_confidence, response_reasons, response_high_stakes
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            task_id,
            session_id.strip(),
            goal.strip(),
            clean_type,
            area.strip(),
            project_hint.strip(),
            context_hint.strip(),
            _as_json(files),
            _as_json(plan),
            _as_json(known_facts),
            _as_json(unknowns),
            _as_json(constraints),
            _as_json(evidence_refs),
            verification_step.strip(),
            cortex_mode.strip(),
            cortex_check_id.strip(),
            cortex_blocked_reason.strip(),
            _as_json(cortex_warnings),
            _as_json(cortex_rules),
            _as_bool(opened_with_guard),
            _as_bool(opened_with_rules),
            _as_bool(guard_has_blocking),
            _as_bool(guard_acknowledged),
            guard_acknowledged_at.strip()[:64] or None,
            guard_summary[:4000],
            _as_bool(must_verify),
            _as_bool(must_change_log),
            _as_bool(must_learning_if_corrected),
            _as_bool(must_write_diary_on_close),
            response_mode.strip(),
            max(0, int(response_confidence or 0)),
            _as_json(response_reasons),
            _as_bool(response_high_stakes),
        ),
    )
    conn.commit()
    return get_protocol_task(task_id)


def set_protocol_task_guard_acknowledged(
    task_id: str,
    *,
    acknowledged: bool = True,
) -> dict:
    conn = get_db()
    if acknowledged:
        conn.execute(
            """UPDATE protocol_tasks
               SET guard_acknowledged = 1,
                   guard_acknowledged_at = COALESCE(guard_acknowledged_at, datetime('now'))
               WHERE task_id = ?""",
            (task_id,),
        )
    else:
        conn.execute(
            """UPDATE protocol_tasks
               SET guard_acknowledged = 0,
                   guard_acknowledged_at = NULL
               WHERE task_id = ?""",
            (task_id,),
        )
    conn.commit()
    return get_protocol_task(task_id) or {}


def get_protocol_task(task_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM protocol_tasks WHERE task_id = ?", (task_id,)).fetchone()
    return _row_to_dict(row)


def create_cortex_evaluation(
    *,
    session_id: str = "",
    task_id: str = "",
    goal: str,
    task_type: str = "",
    area: str = "",
    impact_level: str = "high",
    context_hint: str = "",
    alternatives,
    scores,
    recommended_choice: str,
    recommended_reasoning: str,
    linked_outcome_id: int | None = None,
    goal_profile_id: str = "",
    goal_profile_labels=None,
    goal_profile_weights=None,
    heuristic_choice: str = "",
    heuristic_reasoning: str = "",
    critique_payload=None,
    decision_mode: str = "heuristic",
    selected_choice: str = "",
    selection_reason: str = "",
    selection_source: str = "recommended",
) -> dict:
    conn = get_db()
    clean_level = validate_impact_level(impact_level)
    cursor = conn.execute(
        """INSERT INTO cortex_evaluations (
               session_id, task_id, goal, task_type, area, impact_level, context_hint,
               alternatives, scores, recommended_choice, recommended_reasoning, linked_outcome_id,
               goal_profile_id, goal_profile_labels, goal_profile_weights,
               heuristic_choice, heuristic_reasoning, critique_payload, decision_mode,
               selected_choice, selection_reason, selection_source
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id.strip(),
            task_id.strip(),
            goal.strip(),
            task_type.strip(),
            area.strip(),
            clean_level,
            context_hint.strip(),
            _as_json(alternatives),
            _as_json(scores),
            recommended_choice.strip(),
            recommended_reasoning.strip(),
            int(linked_outcome_id) if linked_outcome_id else None,
            goal_profile_id.strip(),
            _as_json(goal_profile_labels or []),
            _as_json(goal_profile_weights or {}),
            (heuristic_choice or recommended_choice).strip(),
            (heuristic_reasoning or recommended_reasoning).strip(),
            _as_json(critique_payload or {}),
            (decision_mode or "heuristic").strip(),
            (selected_choice or recommended_choice).strip(),
            (selection_reason or recommended_reasoning).strip(),
            (selection_source or "recommended").strip(),
        ),
    )
    conn.commit()
    return get_cortex_evaluation(cursor.lastrowid) or {}


def get_cortex_evaluation(evaluation_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM cortex_evaluations WHERE id = ?",
        (int(evaluation_id),),
    ).fetchone()
    return _row_to_dict(row)


def list_cortex_evaluations(*, session_id: str = "", task_id: str = "", limit: int = 20) -> list[dict]:
    conn = get_db()
    clauses = []
    params: list[object] = []
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id.strip())
    if task_id:
        clauses.append("task_id = ?")
        params.append(task_id.strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM cortex_evaluations {where} ORDER BY created_at DESC, id DESC LIMIT ?",
        params + [max(1, int(limit))],
    ).fetchall()
    return [dict(row) for row in rows]


def latest_cortex_evaluation_for_task(task_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        """SELECT * FROM cortex_evaluations
           WHERE task_id = ?
           ORDER BY created_at DESC, id DESC
           LIMIT 1""",
        (task_id.strip(),),
    ).fetchone()
    return _row_to_dict(row)


def task_has_cortex_evaluation(task_id: str) -> bool:
    if not task_id.strip():
        return False
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM cortex_evaluations WHERE task_id = ? LIMIT 1",
        (task_id.strip(),),
    ).fetchone()
    return bool(row)


def override_cortex_evaluation(evaluation_id: int, *, selected_choice: str, selection_reason: str) -> dict | None:
    conn = get_db()
    conn.execute(
        """UPDATE cortex_evaluations
           SET selected_choice = ?,
               selection_reason = ?,
               selection_source = 'override',
               updated_at = datetime('now')
           WHERE id = ?""",
        (
            selected_choice.strip(),
            selection_reason.strip(),
            int(evaluation_id),
        ),
    )
    conn.commit()
    return get_cortex_evaluation(evaluation_id)


def cortex_evaluation_summary(days: int = 30) -> dict:
    conn = get_db()
    window = f"-{max(1, int(days))} days"
    rows = conn.execute(
        """SELECT e.id, e.goal, e.area, e.goal_profile_id, e.linked_outcome_id,
                  e.recommended_choice, e.selected_choice, e.selection_source, e.created_at,
                  o.status AS outcome_status
           FROM cortex_evaluations e
           LEFT JOIN outcomes o ON o.id = e.linked_outcome_id
           WHERE e.created_at >= datetime('now', ?)
           ORDER BY e.created_at DESC, e.id DESC""",
        (window,),
    ).fetchall()
    items = [dict(row) for row in rows]
    total = len(items)
    overrides = [
        row for row in items
        if (row.get("selection_source") == "override")
        or ((row.get("selected_choice") or "").strip() and (row.get("selected_choice") or "").strip() != (row.get("recommended_choice") or "").strip())
    ]
    accepted = total - len(overrides)
    linked = [row for row in items if row.get("linked_outcome_id")]
    linked_met = [row for row in linked if row.get("outcome_status") == "met"]
    linked_missed = [row for row in linked if row.get("outcome_status") == "missed"]
    linked_pending = [row for row in linked if row.get("outcome_status") == "pending"]
    linked_resolved = [row for row in linked if row.get("outcome_status") in {"met", "missed"}]

    recommended_linked = [row for row in linked_resolved if row not in overrides]
    override_linked = [row for row in linked_resolved if row in overrides]

    def _pct(numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return round((numerator / denominator) * 100, 1)

    profile_counts: dict[str, int] = {}
    for row in items:
        key = (row.get("goal_profile_id") or "unprofiled").strip() or "unprofiled"
        profile_counts[key] = profile_counts.get(key, 0) + 1

    top_profiles = [
        {"goal_profile_id": profile_id, "count": count}
        for profile_id, count in sorted(profile_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]

    gaps: list[str] = []
    if total < 3:
        gaps.append("Muy pocas evaluaciones del cortex para inferir mejora estable.")
    if len(linked) < 2:
        gaps.append("Muy pocas decisiones enlazadas a outcomes para medir calidad real de recomendación.")

    return {
        "days": max(1, int(days)),
        "total_evaluations": total,
        "accepted_recommendations": accepted,
        "overrides": len(overrides),
        "recommendation_accept_rate": _pct(accepted, total),
        "override_rate": _pct(len(overrides), total),
        "linked_outcomes_total": len(linked),
        "linked_outcomes_met": len(linked_met),
        "linked_outcomes_missed": len(linked_missed),
        "linked_outcomes_pending": len(linked_pending),
        "linked_outcome_success_rate": _pct(len(linked_met), len(linked_resolved)),
        "recommended_success_rate": _pct(
            sum(1 for row in recommended_linked if row.get("outcome_status") == "met"),
            len(recommended_linked),
        ),
        "override_success_rate": _pct(
            sum(1 for row in override_linked if row.get("outcome_status") == "met"),
            len(override_linked),
        ),
        "top_goal_profiles": top_profiles,
        "gaps": gaps,
    }


def close_protocol_task(
    task_id: str,
    *,
    outcome: str,
    evidence: str = "",
    files_changed=None,
    correction_happened: bool = False,
    change_log_id: int | None = None,
    learning_id: int | None = None,
    followup_id: str = "",
    outcome_notes: str = "",
) -> dict:
    conn = get_db()
    clean_outcome = validate_close_outcome(outcome)
    conn.execute(
        """UPDATE protocol_tasks
           SET status = ?,
               close_evidence = ?,
               files_changed = ?,
               correction_happened = ?,
               change_log_id = ?,
               learning_id = ?,
               followup_id = ?,
               outcome_notes = ?,
               closed_at = datetime('now')
           WHERE task_id = ?""",
        (
            clean_outcome,
            evidence[:4000],
            _as_json(files_changed),
            _as_bool(correction_happened),
            change_log_id,
            learning_id,
            followup_id[:120],
            outcome_notes[:4000],
            task_id,
        ),
    )
    conn.commit()
    return get_protocol_task(task_id) or {}


def create_protocol_debt(
    session_id: str,
    debt_type: str,
    *,
    severity: str = "warn",
    task_id: str = "",
    evidence: str = "",
) -> dict:
    conn = get_db()
    cursor = conn.execute(
        """INSERT INTO protocol_debt (session_id, task_id, debt_type, severity, evidence)
           VALUES (?, ?, ?, ?, ?)""",
        (
            session_id.strip(),
            task_id.strip(),
            debt_type.strip(),
            severity if severity in {"info", "warn", "error"} else "warn",
            evidence[:4000],
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM protocol_debt WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


def resolve_protocol_debts(
    *,
    session_id: str = "",
    task_id: str = "",
    debt_ids: list[int] | None = None,
    debt_types: list[str] | None = None,
    resolution: str = "",
) -> int:
    conn = get_db()
    clauses = ["status = 'open'"]
    params: list[str] = []
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id.strip())
    if task_id:
        clauses.append("task_id = ?")
        params.append(task_id.strip())
    if debt_ids:
        normalized_ids = [int(item) for item in debt_ids if str(item).strip()]
        if normalized_ids:
            placeholders = ",".join("?" * len(normalized_ids))
            clauses.append(f"id IN ({placeholders})")
            params.extend(normalized_ids)
    if debt_types:
        placeholders = ",".join("?" * len(debt_types))
        clauses.append(f"debt_type IN ({placeholders})")
        params.extend([item.strip() for item in debt_types if item.strip()])
    where = " AND ".join(clauses)
    cursor = conn.execute(
        f"""UPDATE protocol_debt
            SET status = 'resolved',
                resolution = ?,
                resolved_at = datetime('now')
            WHERE {where}""",
        [resolution[:4000]] + params,
    )
    conn.commit()
    return cursor.rowcount


def list_protocol_debts(
    *,
    status: str = "open",
    task_id: str = "",
    session_id: str = "",
    debt_type: str = "",
    severity: str = "",
    limit: int = 50,
) -> list[dict]:
    conn = get_db()
    clauses = []
    params: list[object] = []
    if status in VALID_DEBT_STATUS:
        clauses.append("status = ?")
        params.append(status)
    if task_id:
        clauses.append("task_id = ?")
        params.append(task_id.strip())
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id.strip())
    if debt_type:
        clauses.append("debt_type = ?")
        params.append(debt_type.strip())
    if severity in {"info", "warn", "error"}:
        clauses.append("severity = ?")
        params.append(severity)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM protocol_debt {where} ORDER BY created_at DESC LIMIT ?",
        params + [max(1, int(limit))],
    ).fetchall()
    return [dict(row) for row in rows]


def record_session_correction_requirement(
    session_id: str,
    correction_text: str,
    *,
    source: str = "heartbeat",
) -> dict:
    """Persist that a detected user correction requires a learning_add."""
    conn = get_db()
    clean_sid = str(session_id or "").strip()
    if not clean_sid:
        return {"ok": False, "error": "session_id is required"}
    clean_text = " ".join(str(correction_text or "").strip().split())
    context_hash = _correction_context_hash(clean_sid, clean_text)
    conn.execute(
        """INSERT OR IGNORE INTO session_correction_requirements
           (session_id, context_hash, correction_text, source)
           VALUES (?, ?, ?, ?)""",
        (clean_sid, context_hash, clean_text[:4000], str(source or "heartbeat").strip()[:80]),
    )
    conn.commit()
    row = conn.execute(
        """SELECT *
           FROM session_correction_requirements
           WHERE session_id = ? AND context_hash = ?
           LIMIT 1""",
        (clean_sid, context_hash),
    ).fetchone()
    out = _row_to_dict(row) or {}
    out["ok"] = True
    return out


def list_session_correction_requirements(
    *,
    session_id: str = "",
    status: str = "open",
    limit: int = 50,
) -> list[dict]:
    conn = get_db()
    clauses: list[str] = []
    params: list[object] = []
    if session_id:
        clauses.append("session_id = ?")
        params.append(str(session_id).strip())
    if status:
        clauses.append("status = ?")
        params.append(str(status).strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""SELECT *
            FROM session_correction_requirements
            {where}
            ORDER BY detected_at DESC, id DESC
            LIMIT ?""",
        params + [max(1, int(limit or 50))],
    ).fetchall()
    return [dict(row) for row in rows]


def session_has_open_correction_requirement(session_id: str) -> bool:
    if not str(session_id or "").strip():
        return False
    conn = get_db()
    row = conn.execute(
        """SELECT 1
           FROM session_correction_requirements
           WHERE session_id = ? AND status = 'open'
           LIMIT 1""",
        (str(session_id).strip(),),
    ).fetchone()
    return bool(row)


def resolve_session_correction_requirements(
    *,
    session_id: str = "",
    learning_id: int | None = None,
) -> int:
    """Resolve open correction requirements after a real learning_add."""
    conn = get_db()
    clean_sid = str(session_id or "").strip()
    if not clean_sid:
        rows = conn.execute(
            """SELECT session_id, COUNT(*) AS total
               FROM session_correction_requirements
               WHERE status = 'open'
               GROUP BY session_id
               ORDER BY MAX(detected_at) DESC"""
        ).fetchall()
        if len(rows) == 1:
            clean_sid = str(rows[0]["session_id"] or "").strip()
        else:
            try:
                row = conn.execute(
                    """SELECT r.session_id
                       FROM session_correction_requirements r
                       LEFT JOIN sessions s ON s.sid = r.session_id
                       WHERE r.status = 'open'
                       ORDER BY COALESCE(s.last_heartbeat_ts, s.last_update_epoch, s.started_epoch, 0) DESC,
                                r.detected_at DESC
                       LIMIT 1"""
                ).fetchone()
            except Exception:
                row = None
            clean_sid = str(row["session_id"] or "").strip() if row else ""
        if not clean_sid:
            return 0
    cursor = conn.execute(
        """UPDATE session_correction_requirements
           SET status = 'resolved',
               resolved_at = datetime('now'),
               resolved_learning_id = ?
           WHERE session_id = ? AND status = 'open'""",
        (int(learning_id) if learning_id else None, clean_sid),
    )
    conn.commit()
    if cursor.rowcount:
        resolve_protocol_debts(
            session_id=clean_sid,
            debt_types=["missing_learning_after_correction"],
            resolution=f"Learning #{learning_id} persisted after user correction.",
        )
    return int(cursor.rowcount or 0)


def correction_requirement_summary(session_id: str = "") -> dict:
    conn = get_db()
    clauses: list[str] = []
    params: list[object] = []
    if session_id:
        clauses.append("session_id = ?")
        params.append(str(session_id).strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""SELECT status, COUNT(*) AS total
            FROM session_correction_requirements
            {where}
            GROUP BY status"""
    ).fetchall()
    counts = {str(row["status"]): int(row["total"] or 0) for row in rows}
    return {
        "session_id": str(session_id or "").strip(),
        "corrections_detected": sum(counts.values()),
        "learnings_persisted": counts.get("resolved", 0),
        "open_requirements": counts.get("open", 0),
        "by_status": counts,
    }


def protocol_compliance_summary(days: int = 7) -> dict:
    conn = get_db()
    window = f"-{max(1, int(days))} days"
    tasks = conn.execute(
        """SELECT * FROM protocol_tasks
           WHERE opened_at >= datetime('now', ?)
           ORDER BY opened_at DESC""",
        (window,),
    ).fetchall()
    open_debts = conn.execute(
        """SELECT severity, debt_type, COUNT(*) AS total
           FROM protocol_debt
           WHERE status = 'open' AND created_at >= datetime('now', ?)
           GROUP BY severity, debt_type
           ORDER BY total DESC, debt_type ASC""",
        (window,),
    ).fetchall()

    closed_tasks = [dict(row) for row in tasks if row["status"] != "open"]
    verify_required = [row for row in closed_tasks if row["must_verify"] and row["status"] == "done"]
    verify_ok = [row for row in verify_required if (row.get("close_evidence") or "").strip()]
    change_required = [row for row in closed_tasks if row["must_change_log"]]
    change_ok = [row for row in change_required if row["change_log_id"]]
    learning_required = [row for row in closed_tasks if row["correction_happened"]]
    learning_ok = [row for row in learning_required if row["learning_id"]]
    action_tasks = [row for row in tasks if row["task_type"] in ("edit", "execute", "delegate")]
    cortex_ok = [row for row in action_tasks if row["cortex_mode"] == "act"]
    has_response_high_stakes = bool(tasks) and "response_high_stakes" in tasks[0].keys()
    high_stakes_action_tasks = [row for row in action_tasks if row["response_high_stakes"]] if has_response_high_stakes else []
    decision_ok = [row for row in high_stakes_action_tasks if task_has_cortex_evaluation(row["task_id"])]

    score_parts = []
    if verify_required:
        score_parts.append((len(verify_ok) / len(verify_required)) * 100)
    if change_required:
        score_parts.append((len(change_ok) / len(change_required)) * 100)
    if learning_required:
        score_parts.append((len(learning_ok) / len(learning_required)) * 100)
    if action_tasks:
        score_parts.append((len(cortex_ok) / len(action_tasks)) * 100)
    if high_stakes_action_tasks:
        score_parts.append((len(decision_ok) / len(high_stakes_action_tasks)) * 100)

    base_score = (sum(score_parts) / len(score_parts)) if score_parts else (100.0 if tasks else 0.0)
    warn_debt = sum(row["total"] for row in open_debts if row["severity"] == "warn")
    error_debt = sum(row["total"] for row in open_debts if row["severity"] == "error")
    debt_penalty = min(60, (warn_debt * 5) + (error_debt * 20))
    overall = max(0.0, round(base_score - debt_penalty, 1))

    return {
        "days": max(1, int(days)),
        "tasks_total": len(tasks),
        "tasks_closed": len(closed_tasks),
        "verify_required": len(verify_required),
        "verify_ok": len(verify_ok),
        "verify_pct": round((len(verify_ok) / len(verify_required)) * 100, 1) if verify_required else None,
        "change_required": len(change_required),
        "change_ok": len(change_ok),
        "change_pct": round((len(change_ok) / len(change_required)) * 100, 1) if change_required else None,
        "learning_required": len(learning_required),
        "learning_ok": len(learning_ok),
        "learning_pct": round((len(learning_ok) / len(learning_required)) * 100, 1) if learning_required else None,
        "action_tasks": len(action_tasks),
        "cortex_ok": len(cortex_ok),
        "cortex_pct": round((len(cortex_ok) / len(action_tasks)) * 100, 1) if action_tasks else None,
        "high_stakes_action_tasks": len(high_stakes_action_tasks),
        "decision_support_ok": len(decision_ok),
        "decision_support_pct": round((len(decision_ok) / len(high_stakes_action_tasks)) * 100, 1) if high_stakes_action_tasks else None,
        "open_debt_total": warn_debt + error_debt,
        "open_warn_debt": warn_debt,
        "open_error_debt": error_debt,
        "open_debt_breakdown": [dict(row) for row in open_debts],
        "overall_compliance_pct": overall,
    }
