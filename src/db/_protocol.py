from __future__ import annotations
"""NEXO DB — Protocol discipline runtime."""

import json
import secrets
import time

from db._core import get_db

VALID_TASK_TYPES = {"answer", "analyze", "edit", "execute", "delegate"}
VALID_OUTCOMES = {"open", "done", "partial", "blocked", "failed", "cancelled"}
VALID_DEBT_STATUS = {"open", "forgiven", "resolved"}


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
    clean_type = task_type if task_type in VALID_TASK_TYPES else "answer"
    conn.execute(
        """INSERT INTO protocol_tasks (
               task_id, session_id, goal, task_type, area, project_hint, context_hint,
               files, plan, known_facts, unknowns, constraints, evidence_refs, verification_step,
               cortex_mode, cortex_check_id, cortex_blocked_reason, cortex_warnings, cortex_rules,
               opened_with_guard, opened_with_rules, guard_has_blocking, guard_summary,
               must_verify, must_change_log, must_learning_if_corrected, must_write_diary_on_close,
               response_mode, response_confidence, response_reasons, response_high_stakes
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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


def get_protocol_task(task_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM protocol_tasks WHERE task_id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


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
    clean_outcome = outcome if outcome in VALID_OUTCOMES else "failed"
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


def list_protocol_debts(*, status: str = "open", task_id: str = "", limit: int = 50) -> list[dict]:
    conn = get_db()
    clauses = []
    params: list[object] = []
    if status in VALID_DEBT_STATUS:
        clauses.append("status = ?")
        params.append(status)
    if task_id:
        clauses.append("task_id = ?")
        params.append(task_id.strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM protocol_debt {where} ORDER BY created_at DESC LIMIT ?",
        params + [max(1, int(limit))],
    ).fetchall()
    return [dict(row) for row in rows]


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

    score_parts = []
    if verify_required:
        score_parts.append((len(verify_ok) / len(verify_required)) * 100)
    if change_required:
        score_parts.append((len(change_ok) / len(change_required)) * 100)
    if learning_required:
        score_parts.append((len(learning_ok) / len(learning_required)) * 100)
    if action_tasks:
        score_parts.append((len(cortex_ok) / len(action_tasks)) * 100)

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
        "open_debt_total": warn_debt + error_debt,
        "open_warn_debt": warn_debt,
        "open_error_debt": error_debt,
        "open_debt_breakdown": [dict(row) for row in open_debts],
        "overall_compliance_pct": overall,
    }
