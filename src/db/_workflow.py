from __future__ import annotations
"""NEXO DB — Durable workflow runtime."""

import json
import secrets
import time
from datetime import datetime, timezone

from db._core import get_db

RUN_STATUSES = {
    "open",
    "running",
    "blocked",
    "waiting_approval",
    "failed",
    "completed",
    "cancelled",
}
STEP_STATUSES = {
    "pending",
    "running",
    "blocked",
    "waiting_approval",
    "failed",
    "completed",
    "skipped",
    "retrying",
}
GOAL_STATUSES = {
    "active",
    "blocked",
    "abandoned",
    "completed",
    "cancelled",
}
PRIORITIES = {"low", "normal", "high", "critical"}
RUN_CLOSED_STATUSES = {"completed", "failed", "cancelled"}
GOAL_CLOSED_STATUSES = {"abandoned", "completed", "cancelled"}


def _workflow_run_id() -> str:
    return f"WF-{int(time.time())}-{secrets.randbelow(100000)}"


def _workflow_goal_id() -> str:
    return f"WG-{int(time.time())}-{secrets.randbelow(100000)}"


def _now_sql() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _as_json(value, default):
    if value is None:
        value = default
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _parse_json(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _row_to_step(row) -> dict:
    step = dict(row)
    step["human_gate"] = bool(step.get("human_gate"))
    step["requires_approval"] = bool(step.get("requires_approval"))
    step["last_state_patch"] = _parse_json(step.get("last_state_patch"), {})
    return step


def _goal_closed_at(status: str) -> str | None:
    return _now_sql() if status in GOAL_CLOSED_STATUSES else None


def _row_to_goal(row, *, include_runs: bool = False) -> dict:
    goal = dict(row)
    goal["shared_state"] = _parse_json(goal.get("shared_state"), {})
    goal["open_run_count"] = int(goal.get("open_run_count") or 0)
    goal["run_count"] = int(goal.get("run_count") or 0)
    goal["child_count"] = int(goal.get("child_count") or 0)
    if include_runs:
        goal["runs"] = list_workflow_runs(goal_id=goal["goal_id"], include_closed=True, limit=50)
    return goal


def get_workflow_goal(goal_id: str, *, include_runs: bool = False) -> dict | None:
    conn = get_db()
    row = conn.execute(
        """SELECT g.*,
                  COALESCE((SELECT COUNT(*) FROM workflow_runs r WHERE r.goal_id = g.goal_id), 0) AS run_count,
                  COALESCE((SELECT COUNT(*) FROM workflow_runs r WHERE r.goal_id = g.goal_id
                            AND r.status NOT IN ('completed', 'failed', 'cancelled')), 0) AS open_run_count,
                  COALESCE((SELECT COUNT(*) FROM workflow_goals child WHERE child.parent_goal_id = g.goal_id), 0) AS child_count
           FROM workflow_goals g
           WHERE g.goal_id = ?""",
        (goal_id.strip(),),
    ).fetchone()
    return _row_to_goal(row, include_runs=include_runs) if row else None


def list_workflow_steps(run_id: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT *
           FROM workflow_steps
           WHERE run_id = ?
           ORDER BY step_index ASC, id ASC""",
        (run_id,),
    ).fetchall()
    return [_row_to_step(row) for row in rows]


def _row_to_run(row, *, include_steps: bool = True) -> dict:
    run = dict(row)
    run["shared_state"] = _parse_json(run.get("shared_state"), {})
    if include_steps:
        run["steps"] = list_workflow_steps(run["run_id"])
    return run


def get_workflow_run(run_id: str, *, include_steps: bool = True) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM workflow_runs WHERE run_id = ?",
        (run_id.strip(),),
    ).fetchone()
    return _row_to_run(row, include_steps=include_steps) if row else None


def create_workflow_goal(
    session_id: str,
    title: str,
    *,
    objective: str = "",
    parent_goal_id: str = "",
    priority: str = "normal",
    owner: str = "",
    next_action: str = "",
    success_signal: str = "",
    shared_state=None,
) -> dict:
    conn = get_db()
    clean_parent_goal_id = parent_goal_id.strip()
    if clean_parent_goal_id:
        parent = get_workflow_goal(clean_parent_goal_id)
        if not parent:
            raise ValueError(f"Unknown parent_goal_id: {clean_parent_goal_id}")

    goal_id = _workflow_goal_id()
    clean_priority = priority if priority in PRIORITIES else "normal"
    conn.execute(
        """INSERT INTO workflow_goals (
               goal_id, session_id, title, objective, parent_goal_id, status,
               priority, owner, next_action, success_signal, blocker_reason, shared_state
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            goal_id,
            session_id.strip(),
            title.strip(),
            objective.strip(),
            clean_parent_goal_id,
            "active",
            clean_priority,
            owner.strip(),
            next_action.strip(),
            success_signal.strip(),
            "",
            _as_json(shared_state, {}),
        ),
    )
    conn.commit()
    return get_workflow_goal(goal_id) or {"goal_id": goal_id}


def update_workflow_goal(
    goal_id: str,
    *,
    status: str = "",
    title: str = "",
    objective: str = "",
    parent_goal_id: str = "",
    owner: str = "",
    next_action: str = "",
    success_signal: str = "",
    blocker_reason: str = "",
    shared_state=None,
) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM workflow_goals WHERE goal_id = ?", (goal_id.strip(),)).fetchone()
    if not row:
        return None
    goal = dict(row)

    clean_status = status.strip().lower() if status.strip().lower() in GOAL_STATUSES else goal["status"]
    clean_parent_goal_id = parent_goal_id.strip() if parent_goal_id else goal.get("parent_goal_id", "")
    if clean_parent_goal_id and clean_parent_goal_id != goal_id.strip():
        parent = get_workflow_goal(clean_parent_goal_id)
        if not parent:
            raise ValueError(f"Unknown parent_goal_id: {clean_parent_goal_id}")
    elif clean_parent_goal_id == goal_id.strip():
        raise ValueError("parent_goal_id cannot equal goal_id")

    effective_shared_state = goal["shared_state"] if shared_state is None else _as_json(shared_state, {})
    effective_blocker = blocker_reason.strip() if blocker_reason else goal.get("blocker_reason", "")
    if clean_status != "blocked" and blocker_reason.strip() == "":
        effective_blocker = ""

    conn.execute(
        """UPDATE workflow_goals
           SET title = ?,
               objective = ?,
               parent_goal_id = ?,
               status = ?,
               owner = ?,
               next_action = ?,
               success_signal = ?,
               blocker_reason = ?,
               shared_state = ?,
               updated_at = datetime('now'),
               closed_at = ?
           WHERE goal_id = ?""",
        (
            title.strip() or goal["title"],
            objective.strip() or goal.get("objective", ""),
            clean_parent_goal_id,
            clean_status,
            owner.strip() or goal.get("owner", ""),
            next_action.strip() or goal.get("next_action", ""),
            success_signal.strip() or goal.get("success_signal", ""),
            effective_blocker,
            effective_shared_state,
            _goal_closed_at(clean_status),
            goal_id.strip(),
        ),
    )
    conn.commit()
    return get_workflow_goal(goal_id.strip())


def create_workflow_goal(
    session_id: str,
    title: str,
    *,
    objective: str = "",
    parent_goal_id: str = "",
    priority: str = "normal",
    owner: str = "",
    next_action: str = "",
    success_signal: str = "",
    shared_state=None,
) -> dict:
    conn = get_db()
    clean_parent_goal_id = parent_goal_id.strip()
    if clean_parent_goal_id and not get_workflow_goal(clean_parent_goal_id, include_runs=False):
        raise ValueError(f"Unknown parent_goal_id: {clean_parent_goal_id}")

    goal_id = _workflow_goal_id()
    clean_priority = priority if priority in PRIORITIES else "normal"
    conn.execute(
        """INSERT INTO workflow_goals (
               goal_id, session_id, title, objective, parent_goal_id,
               status, priority, owner, next_action, success_signal, shared_state
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            goal_id,
            session_id.strip(),
            title.strip(),
            objective.strip(),
            clean_parent_goal_id,
            "active",
            clean_priority,
            owner.strip(),
            next_action.strip(),
            success_signal.strip(),
            _as_json(shared_state, {}),
        ),
    )
    conn.commit()
    goal = get_workflow_goal(goal_id)
    return goal or {"goal_id": goal_id, "status": "active"}


def update_workflow_goal(
    goal_id: str,
    *,
    status: str = "",
    title: str = "",
    objective: str = "",
    parent_goal_id: str = "",
    owner: str = "",
    next_action: str = "",
    success_signal: str = "",
    blocker_reason: str = "",
    shared_state=None,
) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM workflow_goals WHERE goal_id = ?", (goal_id.strip(),)).fetchone()
    if not row:
        return None

    goal = dict(row)
    clean_status = status.strip().lower() if status.strip().lower() in GOAL_STATUSES else goal["status"]
    clean_parent_goal_id = parent_goal_id.strip() if parent_goal_id.strip() else goal.get("parent_goal_id", "")
    if clean_parent_goal_id and clean_parent_goal_id != goal_id.strip() and not get_workflow_goal(clean_parent_goal_id):
        raise ValueError(f"Unknown parent_goal_id: {clean_parent_goal_id}")
    if clean_parent_goal_id == goal_id.strip():
        raise ValueError("goal_id cannot be its own parent")

    effective_shared_state = goal["shared_state"]
    if shared_state is not None:
        effective_shared_state = _as_json(shared_state, {})

    conn.execute(
        """UPDATE workflow_goals
           SET title = ?,
               objective = ?,
               parent_goal_id = ?,
               status = ?,
               owner = ?,
               next_action = ?,
               success_signal = ?,
               blocker_reason = ?,
               shared_state = ?,
               updated_at = datetime('now'),
               closed_at = ?
           WHERE goal_id = ?""",
        (
            title.strip() or goal["title"],
            objective.strip() or goal.get("objective", ""),
            clean_parent_goal_id,
            clean_status,
            owner.strip() or goal.get("owner", ""),
            next_action.strip() or goal.get("next_action", ""),
            success_signal.strip() or goal.get("success_signal", ""),
            blocker_reason.strip() or goal.get("blocker_reason", ""),
            effective_shared_state,
            _goal_closed_at(clean_status),
            goal_id.strip(),
        ),
    )
    conn.commit()
    return get_workflow_goal(goal_id.strip())


def list_workflow_goals(*, status: str = "", include_closed: bool = False, limit: int = 20) -> list[dict]:
    conn = get_db()
    clauses = []
    params: list[object] = []
    if status.strip() in GOAL_STATUSES:
        clauses.append("g.status = ?")
        params.append(status.strip())
    elif not include_closed:
        clauses.append("g.status NOT IN ('abandoned', 'completed', 'cancelled')")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""SELECT g.*,
                  COALESCE((SELECT COUNT(*) FROM workflow_runs r WHERE r.goal_id = g.goal_id), 0) AS run_count,
                  COALESCE((SELECT COUNT(*) FROM workflow_runs r WHERE r.goal_id = g.goal_id
                            AND r.status NOT IN ('completed', 'failed', 'cancelled')), 0) AS open_run_count,
                  COALESCE((SELECT COUNT(*) FROM workflow_goals child WHERE child.parent_goal_id = g.goal_id), 0) AS child_count
           FROM workflow_goals g
           {where}
           ORDER BY g.updated_at DESC, g.opened_at DESC
           LIMIT ?""",
        params + [max(1, int(limit))],
    ).fetchall()
    return [_row_to_goal(row, include_runs=False) for row in rows]


def _find_reusable_run(session_id: str, idempotency_key: str, goal_id: str = "") -> dict | None:
    if not session_id.strip() or not idempotency_key.strip():
        return None
    conn = get_db()
    clauses = [
        "session_id = ?",
        "idempotency_key = ?",
        "status NOT IN ('completed', 'failed', 'cancelled')",
    ]
    params: list[object] = [session_id.strip(), idempotency_key.strip()]
    if goal_id.strip():
        clauses.append("goal_id = ?")
        params.append(goal_id.strip())
    row = conn.execute(
        f"""SELECT *
           FROM workflow_runs
           WHERE {' AND '.join(clauses)}
           ORDER BY opened_at DESC
           LIMIT 1""",
        params,
    ).fetchone()
    return _row_to_run(row) if row else None


def list_workflow_goals(*, status: str = "", include_closed: bool = False, limit: int = 20) -> list[dict]:
    conn = get_db()
    clauses = []
    params: list[object] = []
    clean_status = status.strip().lower()
    if clean_status in GOAL_STATUSES:
        clauses.append("g.status = ?")
        params.append(clean_status)
    elif not include_closed:
        clauses.append("g.status NOT IN ('abandoned', 'completed', 'cancelled')")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""SELECT g.*,
                  COALESCE((SELECT COUNT(*) FROM workflow_runs r WHERE r.goal_id = g.goal_id), 0) AS run_count,
                  COALESCE((SELECT COUNT(*) FROM workflow_runs r WHERE r.goal_id = g.goal_id
                            AND r.status NOT IN ('completed', 'failed', 'cancelled')), 0) AS open_run_count,
                  COALESCE((SELECT COUNT(*) FROM workflow_goals child WHERE child.parent_goal_id = g.goal_id), 0) AS child_count
           FROM workflow_goals g
           {where}
           ORDER BY g.updated_at DESC, g.opened_at DESC
           LIMIT ?""",
        params + [max(1, int(limit))],
    ).fetchall()
    return [_row_to_goal(row) for row in rows]


def _normalize_step(step, index: int) -> dict:
    if isinstance(step, str):
        title = step.strip()
        key = title.lower().replace(" ", "-")[:80]
        return {
            "step_key": key or f"step-{index}",
            "title": title or f"Step {index}",
            "step_index": index,
            "status": "pending",
            "max_retries": 0,
            "retry_policy": "",
            "retry_after": "",
            "human_gate": False,
            "requires_approval": False,
            "compensation": "",
        }

    step = dict(step or {})
    title = str(step.get("title") or step.get("step_key") or f"Step {index}").strip()
    key = str(step.get("step_key") or title.lower().replace(" ", "-")[:80] or f"step-{index}").strip()
    status = str(step.get("status") or "pending").strip().lower()
    if status not in STEP_STATUSES:
        status = "pending"
    return {
        "step_key": key,
        "title": title,
        "step_index": int(step.get("step_index") or index),
        "status": status,
        "max_retries": max(0, int(step.get("max_retries") or 0)),
        "retry_policy": str(step.get("retry_policy") or "").strip(),
        "retry_after": str(step.get("retry_after") or "").strip(),
        "human_gate": bool(step.get("human_gate")),
        "requires_approval": bool(step.get("requires_approval")),
        "compensation": str(step.get("compensation") or "").strip(),
    }


def create_workflow_run(
    session_id: str,
    goal: str,
    *,
    goal_id: str = "",
    workflow_kind: str = "general",
    protocol_task_id: str = "",
    idempotency_key: str = "",
    priority: str = "normal",
    shared_state=None,
    next_action: str = "",
    owner: str = "",
    steps=None,
) -> dict:
    existing = _find_reusable_run(session_id, idempotency_key, goal_id=goal_id)
    if existing:
        existing["reused_existing"] = True
        return existing

    conn = get_db()
    run_id = _workflow_run_id()
    clean_priority = priority if priority in PRIORITIES else "normal"
    clean_goal_id = goal_id.strip()
    if clean_goal_id:
        linked_goal = get_workflow_goal(clean_goal_id)
        if not linked_goal:
            raise ValueError(f"Unknown goal_id: {clean_goal_id}")
        if linked_goal["status"] in GOAL_CLOSED_STATUSES:
            raise ValueError(f"Goal {clean_goal_id} is closed with status '{linked_goal['status']}'")
    steps = steps or []
    first_step_key = ""
    if steps:
        first_step_key = _normalize_step(steps[0], 1)["step_key"]

    conn.execute(
        """INSERT INTO workflow_runs (
               run_id, session_id, protocol_task_id, goal_id, goal, workflow_kind,
               status, priority, idempotency_key, shared_state,
               next_action, current_step_key, last_checkpoint_label, owner
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            session_id.strip(),
            protocol_task_id.strip(),
            clean_goal_id,
            goal.strip(),
            workflow_kind.strip() or "general",
            "open",
            clean_priority,
            idempotency_key.strip(),
            _as_json(shared_state, {}),
            next_action.strip(),
            first_step_key,
            "opened",
            owner.strip(),
        ),
    )
    for index, raw_step in enumerate(steps, 1):
        step = _normalize_step(raw_step, index)
        conn.execute(
            """INSERT INTO workflow_steps (
                   run_id, step_key, title, step_index, status, max_retries,
                   retry_policy, retry_after, human_gate, requires_approval,
                   compensation
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                step["step_key"],
                step["title"],
                step["step_index"],
                step["status"],
                step["max_retries"],
                step["retry_policy"],
                step["retry_after"],
                1 if step["human_gate"] else 0,
                1 if step["requires_approval"] else 0,
                step["compensation"],
            ),
        )

    conn.execute(
        """INSERT INTO workflow_checkpoints (
               run_id, step_key, checkpoint_label, run_status, step_status,
               summary, shared_state, state_patch, next_action, actor
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            first_step_key,
            "opened",
            "open",
            "pending" if first_step_key else "",
            "Workflow opened",
            _as_json(shared_state, {}),
            _as_json({}, {}),
            next_action.strip(),
            owner.strip() or "workflow-open",
        ),
    )
    conn.commit()
    run = get_workflow_run(run_id)
    if run:
        run["reused_existing"] = False
    return run or {"run_id": run_id, "reused_existing": False}


def _next_pending_step(conn, run_id: str) -> dict | None:
    row = conn.execute(
        """SELECT *
           FROM workflow_steps
           WHERE run_id = ?
             AND status IN ('pending', 'retrying')
           ORDER BY step_index ASC, id ASC
           LIMIT 1""",
        (run_id,),
    ).fetchone()
    return _row_to_step(row) if row else None


def _upsert_workflow_step(
    conn,
    run_id: str,
    *,
    step_key: str,
    step_title: str = "",
    step_status: str = "",
    max_retries: int | None = None,
    retry_policy: str = "",
    retry_after: str = "",
    requires_approval: bool | None = None,
    compensation: str = "",
    state_patch=None,
    summary: str = "",
    evidence: str = "",
) -> dict:
    row = conn.execute(
        "SELECT * FROM workflow_steps WHERE run_id = ? AND step_key = ?",
        (run_id, step_key),
    ).fetchone()
    created = False
    if not row:
        created = True
        conn.execute(
            """INSERT INTO workflow_steps (
                   run_id, step_key, title, step_index, status
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                run_id,
                step_key,
                step_title or step_key,
                999,
                "pending",
            ),
        )
        row = conn.execute(
            "SELECT * FROM workflow_steps WHERE run_id = ? AND step_key = ?",
            (run_id, step_key),
        ).fetchone()

    step = dict(row)
    clean_step_status = step_status if step_status in STEP_STATUSES else step["status"]
    attempt_count = int(step.get("attempt_count") or 0)
    if clean_step_status in {"running", "retrying"} and step.get("status") not in {"running", "retrying"}:
        attempt_count += 1
    elif created and clean_step_status in {"running", "retrying"}:
        attempt_count = 1

    started_at_value = step.get("started_at")
    completed_at_value = step.get("completed_at")
    if clean_step_status in {"running", "retrying"} and not started_at_value:
        started_at_value = _now_sql()
    if clean_step_status in {"completed", "skipped"}:
        completed_at_value = _now_sql()
    elif clean_step_status in {"running", "retrying", "blocked", "waiting_approval", "failed"}:
        completed_at_value = None

    effective_max_retries = int(max_retries) if max_retries is not None else int(step.get("max_retries") or 0)
    effective_requires_approval = (
        bool(requires_approval)
        if requires_approval is not None
        else bool(step.get("requires_approval"))
    )
    conn.execute(
        """UPDATE workflow_steps
            SET title = ?,
                status = ?,
                attempt_count = ?,
                max_retries = ?,
                retry_policy = ?,
                retry_after = ?,
                human_gate = ?,
                requires_approval = ?,
                compensation = ?,
                last_summary = ?,
                last_evidence = ?,
                last_state_patch = ?,
                started_at = ?,
                completed_at = ?,
                updated_at = datetime('now')
            WHERE run_id = ? AND step_key = ?""",
        (
            step_title or step.get("title") or step_key,
            clean_step_status,
            attempt_count,
            effective_max_retries,
            retry_policy or step.get("retry_policy") or "",
            retry_after or step.get("retry_after") or "",
            1 if (bool(step.get("human_gate")) or effective_requires_approval) else 0,
            1 if effective_requires_approval else 0,
            compensation or step.get("compensation") or "",
            summary[:2000],
            evidence[:2000],
            _as_json(state_patch, {}),
            started_at_value,
            completed_at_value,
            run_id,
            step_key,
        ),
    )
    row = conn.execute(
        "SELECT * FROM workflow_steps WHERE run_id = ? AND step_key = ?",
        (run_id, step_key),
    ).fetchone()
    return _row_to_step(row)


def _derive_run_status(conn, run_id: str, *, requested: str = "", step_status: str = "") -> str:
    if requested in RUN_STATUSES:
        return requested
    if step_status == "waiting_approval":
        return "waiting_approval"
    if step_status == "blocked":
        return "blocked"
    if step_status in {"running", "retrying"}:
        return "running"
    if step_status == "failed":
        return "failed"
    if step_status in {"completed", "skipped"}:
        rows = conn.execute(
            "SELECT status FROM workflow_steps WHERE run_id = ? ORDER BY step_index ASC, id ASC",
            (run_id,),
        ).fetchall()
        statuses = [row["status"] for row in rows]
        if statuses and all(status in {"completed", "skipped"} for status in statuses):
            return "completed"
        if statuses:
            return "running"
    row = conn.execute("SELECT status FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone()
    return row["status"] if row else "open"


def record_workflow_transition(
    run_id: str,
    *,
    step_key: str = "",
    step_title: str = "",
    step_status: str = "",
    run_status: str = "",
    checkpoint_label: str = "",
    summary: str = "",
    shared_state=None,
    state_patch=None,
    evidence: str = "",
    next_action: str = "",
    retry_after: str = "",
    max_retries: int | None = None,
    retry_policy: str = "",
    requires_approval: bool | None = None,
    compensation: str = "",
    actor: str = "",
    owner: str = "",
) -> dict | None:
    conn = get_db()
    run_row = conn.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id.strip(),)).fetchone()
    if not run_row:
        return None

    step = None
    clean_step_status = step_status if step_status in STEP_STATUSES else ""
    if step_key.strip():
        step = _upsert_workflow_step(
            conn,
            run_id.strip(),
            step_key=step_key.strip(),
            step_title=step_title.strip(),
            step_status=clean_step_status,
            max_retries=max_retries,
            retry_policy=retry_policy.strip(),
            retry_after=retry_after.strip(),
            requires_approval=requires_approval,
            compensation=compensation.strip(),
            state_patch=state_patch,
            summary=summary.strip(),
            evidence=evidence.strip(),
        )

    clean_run_status = _derive_run_status(
        conn,
        run_id.strip(),
        requested=run_status.strip(),
        step_status=clean_step_status,
    )
    current_step_key = run_row["current_step_key"] or ""
    if step:
        if step["status"] in {"completed", "skipped"}:
            pending = _next_pending_step(conn, run_id.strip())
            current_step_key = pending["step_key"] if pending else ""
        else:
            current_step_key = step["step_key"]
    shared_state_json = run_row["shared_state"]
    if shared_state is not None:
        shared_state_json = _as_json(shared_state, {})
    label = checkpoint_label.strip() or clean_step_status or clean_run_status or "checkpoint"
    conn.execute(
        """UPDATE workflow_runs
            SET status = ?,
                shared_state = ?,
                next_action = ?,
                current_step_key = ?,
                last_checkpoint_label = ?,
                owner = CASE
                    WHEN ? != '' THEN ?
                    ELSE owner
                END,
                updated_at = datetime('now'),
                closed_at = CASE
                    WHEN ? IN ('completed', 'failed', 'cancelled') THEN datetime('now')
                    ELSE NULL
                END
            WHERE run_id = ?""",
        (
            clean_run_status,
            shared_state_json,
            next_action.strip() or run_row["next_action"] or "",
            current_step_key,
            label,
            owner.strip(),
            owner.strip(),
            clean_run_status,
            run_id.strip(),
        ),
    )
    conn.execute(
        """INSERT INTO workflow_checkpoints (
               run_id, step_key, checkpoint_label, run_status, step_status, summary,
               shared_state, state_patch, evidence, next_action, retry_after,
               requires_approval, compensation_note, attempt, actor
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id.strip(),
            step_key.strip(),
            label,
            clean_run_status,
            clean_step_status,
            summary.strip()[:2000],
            shared_state_json,
            _as_json(state_patch, {}),
            evidence.strip()[:2000],
            next_action.strip()[:1000],
            retry_after.strip(),
            1 if bool(requires_approval) else 0,
            compensation.strip()[:1000],
            int(step.get("attempt_count") or 0) if step else 0,
            actor.strip() or "workflow-update",
        ),
    )
    conn.commit()
    return get_workflow_run(run_id.strip())


def list_workflow_runs(*, status: str = "", goal_id: str = "", include_closed: bool = False, limit: int = 20) -> list[dict]:
    conn = get_db()
    clauses = []
    params: list[object] = []
    clean_goal_id = goal_id.strip()
    if clean_goal_id:
        clauses.append("goal_id = ?")
        params.append(clean_goal_id)
    if status.strip() in RUN_STATUSES:
        clauses.append("status = ?")
        params.append(status.strip())
    elif not include_closed:
        clauses.append("status NOT IN ('completed', 'failed', 'cancelled')")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""SELECT *
            FROM workflow_runs
            {where}
            ORDER BY updated_at DESC, opened_at DESC
            LIMIT ?""",
        params + [max(1, int(limit))],
    ).fetchall()
    return [_row_to_run(row, include_steps=False) for row in rows]


def get_workflow_replay(run_id: str, *, limit: int = 50) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT *
           FROM workflow_checkpoints
           WHERE run_id = ?
           ORDER BY id DESC
           LIMIT ?""",
        (run_id.strip(), max(1, int(limit))),
    ).fetchall()
    checkpoints = []
    for row in rows:
        item = dict(row)
        item["shared_state"] = _parse_json(item.get("shared_state"), {})
        item["state_patch"] = _parse_json(item.get("state_patch"), {})
        item["requires_approval"] = bool(item.get("requires_approval"))
        checkpoints.append(item)
    return checkpoints


def get_workflow_resume_state(run_id: str) -> dict | None:
    run = get_workflow_run(run_id.strip())
    if not run:
        return None

    steps = run.get("steps") or []
    current = None
    if run.get("current_step_key"):
        current = next((step for step in steps if step["step_key"] == run["current_step_key"]), None)

    waiting = next((step for step in steps if step["status"] == "waiting_approval"), None)
    if waiting:
        return {
            "run": run,
            "resume_state": "waiting_approval",
            "can_resume": False,
            "requires_approval": True,
            "next_step": waiting,
            "message": f"Workflow is waiting for approval on step '{waiting['title']}'.",
        }

    retryable = next(
        (
            step
            for step in steps
            if step["status"] == "failed"
            and int(step.get("max_retries") or 0) > int(step.get("attempt_count") or 0)
        ),
        None,
    )
    if retryable:
        return {
            "run": run,
            "resume_state": "retry_available",
            "can_resume": True,
            "requires_approval": False,
            "next_step": retryable,
            "message": (
                f"Retry step '{retryable['title']}' "
                f"(attempt {int(retryable.get('attempt_count') or 0) + 1}/{int(retryable.get('max_retries') or 0)})."
            ),
        }

    if current and current["status"] in {"running", "blocked", "retrying"}:
        return {
            "run": run,
            "resume_state": current["status"],
            "can_resume": current["status"] != "blocked",
            "requires_approval": False,
            "next_step": current,
            "message": f"Resume current step '{current['title']}' from status '{current['status']}'.",
        }

    pending = next((step for step in steps if step["status"] in {"pending", "retrying"}), None)
    if pending:
        return {
            "run": run,
            "resume_state": "ready",
            "can_resume": True,
            "requires_approval": False,
            "next_step": pending,
            "message": f"Continue with step '{pending['title']}'.",
        }

    return {
        "run": run,
        "resume_state": run.get("status") or "open",
        "can_resume": run.get("status") not in RUN_CLOSED_STATUSES,
        "requires_approval": False,
        "next_step": current,
        "message": run.get("next_action") or "Workflow state available.",
    }
