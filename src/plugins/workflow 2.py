"""Workflow plugin — durable execution runtime for long multi-step tasks."""

from __future__ import annotations

import json

from db import (
    create_workflow_goal,
    create_workflow_run,
    get_workflow_goal,
    get_protocol_task,
    get_workflow_run,
    get_workflow_replay,
    get_workflow_resume_state,
    list_workflow_goals,
    list_workflow_runs,
    record_workflow_transition,
    update_workflow_goal,
)


def _parse_json_list(value: str) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _parse_json_object(value: str) -> dict | None:
    if value is None:
        return None
    stripped = str(value).strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def handle_workflow_open(
    sid: str,
    goal: str,
    goal_id: str = "",
    workflow_kind: str = "general",
    protocol_task_id: str = "",
    idempotency_key: str = "",
    priority: str = "normal",
    steps: str = "[]",
    shared_state: str = "{}",
    next_action: str = "",
    owner: str = "",
) -> str:
    """Open a durable workflow run for a long multi-step task."""
    clean_sid = (sid or "").strip()
    clean_goal = (goal or "").strip()
    if not clean_sid:
        return json.dumps({"ok": False, "error": "sid is required"}, ensure_ascii=False, indent=2)
    if not clean_goal:
        return json.dumps({"ok": False, "error": "goal is required"}, ensure_ascii=False, indent=2)

    clean_protocol_task_id = (protocol_task_id or "").strip()
    if clean_protocol_task_id:
        task = get_protocol_task(clean_protocol_task_id)
        if not task:
            return json.dumps(
                {"ok": False, "error": f"Unknown protocol_task_id: {clean_protocol_task_id}"},
                ensure_ascii=False,
                indent=2,
            )
        if task.get("session_id") and task["session_id"] != clean_sid:
            return json.dumps(
                {
                    "ok": False,
                    "error": f"Protocol task {clean_protocol_task_id} belongs to {task['session_id']}, not {clean_sid}",
                },
                ensure_ascii=False,
                indent=2,
            )

    try:
        run = create_workflow_run(
            clean_sid,
            clean_goal,
            goal_id=(goal_id or "").strip(),
            workflow_kind=(workflow_kind or "general").strip() or "general",
            protocol_task_id=clean_protocol_task_id,
            idempotency_key=(idempotency_key or "").strip(),
            priority=(priority or "normal").strip().lower(),
            steps=_parse_json_list(steps),
            shared_state=_parse_json_object(shared_state) if str(shared_state).strip() else {},
            next_action=(next_action or "").strip(),
            owner=(owner or "").strip(),
        )
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2)
    if not run:
        return json.dumps({"ok": False, "error": "workflow run could not be created"}, ensure_ascii=False, indent=2)

    step_titles = [step["title"] for step in (run.get("steps") or [])[:8]]
    response = {
        "ok": True,
        "run_id": run["run_id"],
        "status": run["status"],
        "reused_existing": bool(run.get("reused_existing")),
        "goal_id": run.get("goal_id", ""),
        "workflow_kind": run.get("workflow_kind"),
        "protocol_task_id": run.get("protocol_task_id", ""),
        "current_step_key": run.get("current_step_key", ""),
        "next_action": run.get("next_action", ""),
        "step_count": len(run.get("steps") or []),
        "steps": step_titles,
    }
    return json.dumps(response, ensure_ascii=False, indent=2)


def handle_goal_open(
    sid: str,
    title: str,
    objective: str = "",
    parent_goal_id: str = "",
    priority: str = "normal",
    next_action: str = "",
    success_signal: str = "",
    owner: str = "",
    shared_state: str = "{}",
) -> str:
    """Open a durable goal so objectives survive sessions and can own workflows."""
    clean_sid = (sid or "").strip()
    clean_title = (title or "").strip()
    if not clean_sid:
        return json.dumps({"ok": False, "error": "sid is required"}, ensure_ascii=False, indent=2)
    if not clean_title:
        return json.dumps({"ok": False, "error": "title is required"}, ensure_ascii=False, indent=2)
    try:
        goal = create_workflow_goal(
            clean_sid,
            clean_title,
            objective=(objective or "").strip(),
            parent_goal_id=(parent_goal_id or "").strip(),
            priority=(priority or "normal").strip().lower(),
            next_action=(next_action or "").strip(),
            success_signal=(success_signal or "").strip(),
            owner=(owner or "").strip(),
            shared_state=_parse_json_object(shared_state) if str(shared_state).strip() else {},
        )
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2)

    return json.dumps(
        {
            "ok": True,
            "goal_id": goal["goal_id"],
            "status": goal["status"],
            "title": goal["title"],
            "priority": goal.get("priority", "normal"),
            "parent_goal_id": goal.get("parent_goal_id", ""),
            "next_action": goal.get("next_action", ""),
        },
        ensure_ascii=False,
        indent=2,
    )


def handle_goal_update(
    goal_id: str,
    status: str = "",
    title: str = "",
    objective: str = "",
    parent_goal_id: str = "",
    next_action: str = "",
    success_signal: str = "",
    blocker_reason: str = "",
    owner: str = "",
    shared_state: str = "",
) -> str:
    """Update a durable goal with blocked/abandoned/completed state."""
    clean_goal_id = (goal_id or "").strip()
    if not clean_goal_id:
        return json.dumps({"ok": False, "error": "goal_id is required"}, ensure_ascii=False, indent=2)
    try:
        goal = update_workflow_goal(
            clean_goal_id,
            status=(status or "").strip().lower(),
            title=(title or "").strip(),
            objective=(objective or "").strip(),
            parent_goal_id=(parent_goal_id or "").strip(),
            next_action=(next_action or "").strip(),
            success_signal=(success_signal or "").strip(),
            blocker_reason=(blocker_reason or "").strip(),
            owner=(owner or "").strip(),
            shared_state=_parse_json_object(shared_state) if str(shared_state).strip() else None,
        )
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2)
    if not goal:
        return json.dumps({"ok": False, "error": f"Unknown goal_id: {clean_goal_id}"}, ensure_ascii=False, indent=2)

    return json.dumps(
        {
            "ok": True,
            "goal_id": goal["goal_id"],
            "status": goal["status"],
            "title": goal["title"],
            "next_action": goal.get("next_action", ""),
            "blocker_reason": goal.get("blocker_reason", ""),
            "run_count": goal.get("run_count", 0),
            "open_run_count": goal.get("open_run_count", 0),
            "child_count": goal.get("child_count", 0),
        },
        ensure_ascii=False,
        indent=2,
    )


def handle_goal_get(goal_id: str, include_runs: bool = False) -> str:
    """Get one durable goal and optionally include linked workflow runs."""
    clean_goal_id = (goal_id or "").strip()
    if not clean_goal_id:
        return json.dumps({"ok": False, "error": "goal_id is required"}, ensure_ascii=False, indent=2)
    goal = get_workflow_goal(clean_goal_id, include_runs=bool(include_runs))
    if not goal:
        return json.dumps({"ok": False, "error": f"Unknown goal_id: {clean_goal_id}"}, ensure_ascii=False, indent=2)
    return json.dumps({"ok": True, "goal": goal}, ensure_ascii=False, indent=2)


def handle_goal_list(status: str = "", include_closed: bool = False, limit: int = 20) -> str:
    """List durable goals so objectives do not collapse into loose followups."""
    goals = list_workflow_goals(
        status=(status or "").strip().lower(),
        include_closed=bool(include_closed),
        limit=max(1, int(limit or 20)),
    )
    return json.dumps(
        {
            "ok": True,
            "count": len(goals),
            "goals": [
                {
                    "goal_id": goal["goal_id"],
                    "session_id": goal.get("session_id", ""),
                    "title": goal["title"],
                    "status": goal["status"],
                    "priority": goal.get("priority", "normal"),
                    "parent_goal_id": goal.get("parent_goal_id", ""),
                    "next_action": goal.get("next_action", ""),
                    "blocker_reason": goal.get("blocker_reason", ""),
                    "run_count": goal.get("run_count", 0),
                    "open_run_count": goal.get("open_run_count", 0),
                    "child_count": goal.get("child_count", 0),
                    "updated_at": goal["updated_at"],
                }
                for goal in goals
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def handle_workflow_update(
    run_id: str,
    step_key: str = "",
    step_title: str = "",
    step_status: str = "",
    run_status: str = "",
    checkpoint_label: str = "",
    summary: str = "",
    shared_state: str = "",
    state_patch: str = "",
    evidence: str = "",
    next_action: str = "",
    retry_after: str = "",
    max_retries: int = 0,
    retry_policy: str = "",
    requires_approval: bool = False,
    compensation: str = "",
    actor: str = "",
    owner: str = "",
) -> str:
    """Update a workflow run, recording a replayable checkpoint and step state."""
    clean_run_id = (run_id or "").strip()
    if not clean_run_id:
        return json.dumps({"ok": False, "error": "run_id is required"}, ensure_ascii=False, indent=2)

    run = record_workflow_transition(
        clean_run_id,
        step_key=(step_key or "").strip(),
        step_title=(step_title or "").strip(),
        step_status=(step_status or "").strip().lower(),
        run_status=(run_status or "").strip().lower(),
        checkpoint_label=(checkpoint_label or "").strip(),
        summary=(summary or "").strip(),
        shared_state=_parse_json_object(shared_state) if str(shared_state).strip() else None,
        state_patch=_parse_json_object(state_patch) if str(state_patch).strip() else {},
        evidence=(evidence or "").strip(),
        next_action=(next_action or "").strip(),
        retry_after=(retry_after or "").strip(),
        max_retries=max(0, int(max_retries or 0)),
        retry_policy=(retry_policy or "").strip(),
        requires_approval=requires_approval,
        compensation=(compensation or "").strip(),
        actor=(actor or "").strip(),
        owner=(owner or "").strip(),
    )
    if not run:
        return json.dumps({"ok": False, "error": f"Unknown run_id: {clean_run_id}"}, ensure_ascii=False, indent=2)

    resume = get_workflow_resume_state(clean_run_id) or {}
    response = {
        "ok": True,
        "run_id": clean_run_id,
        "status": run["status"],
        "current_step_key": run.get("current_step_key", ""),
        "last_checkpoint_label": run.get("last_checkpoint_label", ""),
        "next_action": run.get("next_action", ""),
        "resume_state": resume.get("resume_state", ""),
        "resume_message": resume.get("message", ""),
    }
    if resume.get("next_step"):
        response["next_step"] = {
            "step_key": resume["next_step"]["step_key"],
            "title": resume["next_step"]["title"],
            "status": resume["next_step"]["status"],
            "attempt_count": resume["next_step"].get("attempt_count", 0),
            "max_retries": resume["next_step"].get("max_retries", 0),
        }
    return json.dumps(response, ensure_ascii=False, indent=2)


def handle_workflow_get(run_id: str, include_steps: bool = True, checkpoint_limit: int = 8) -> str:
    """Read the full durable workflow state, including shared state and recent actors."""
    clean_run_id = (run_id or "").strip()
    if not clean_run_id:
        return json.dumps({"ok": False, "error": "run_id is required"}, ensure_ascii=False, indent=2)
    run = get_workflow_run(clean_run_id, include_steps=bool(include_steps))
    if not run:
        return json.dumps({"ok": False, "error": f"Unknown run_id: {clean_run_id}"}, ensure_ascii=False, indent=2)

    replay = get_workflow_replay(clean_run_id, limit=max(1, int(checkpoint_limit or 8)))
    resume = get_workflow_resume_state(clean_run_id) or {}
    recent_actors: list[str] = []
    for item in replay:
        actor = (item.get("actor") or "").strip()
        if actor and actor not in recent_actors:
            recent_actors.append(actor)

    return json.dumps(
        {
            "ok": True,
            "run": run,
            "resume_state": resume.get("resume_state", ""),
            "can_resume": resume.get("can_resume", False),
            "requires_approval": resume.get("requires_approval", False),
            "recent_actors": recent_actors,
            "checkpoints": [
                {
                    "created_at": item["created_at"],
                    "checkpoint_label": item["checkpoint_label"],
                    "step_key": item["step_key"],
                    "run_status": item["run_status"],
                    "step_status": item["step_status"],
                    "summary": item["summary"],
                    "next_action": item["next_action"],
                    "actor": item.get("actor", ""),
                }
                for item in replay
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def handle_workflow_handoff(
    run_id: str,
    actor: str,
    next_action: str = "",
    handoff_note: str = "",
    shared_state: str = "",
    new_owner: str = "",
) -> str:
    """Record a durable handoff so another agent/client can resume honestly."""
    clean_run_id = (run_id or "").strip()
    clean_actor = (actor or "").strip()
    if not clean_run_id:
        return json.dumps({"ok": False, "error": "run_id is required"}, ensure_ascii=False, indent=2)
    if not clean_actor:
        return json.dumps({"ok": False, "error": "actor is required"}, ensure_ascii=False, indent=2)

    handoff_summary = (handoff_note or f"Handoff recorded by {clean_actor}.").strip()
    run = record_workflow_transition(
        clean_run_id,
        checkpoint_label="handoff",
        summary=handoff_summary,
        shared_state=_parse_json_object(shared_state) if str(shared_state).strip() else None,
        next_action=(next_action or "").strip(),
        actor=clean_actor,
        owner=(new_owner or "").strip(),
    )
    if not run:
        return json.dumps({"ok": False, "error": f"Unknown run_id: {clean_run_id}"}, ensure_ascii=False, indent=2)

    return json.dumps(
        {
            "ok": True,
            "run_id": clean_run_id,
            "status": run["status"],
            "owner": run.get("owner", ""),
            "next_action": run.get("next_action", ""),
            "shared_state": run.get("shared_state", {}),
            "message": f"Handoff checkpoint recorded by {clean_actor}.",
        },
        ensure_ascii=False,
        indent=2,
    )


def handle_workflow_compensation(run_id: str, checkpoint_limit: int = 10) -> str:
    """Return the compensation / rollback plan for a partially completed workflow run."""
    clean_run_id = (run_id or "").strip()
    if not clean_run_id:
        return json.dumps({"ok": False, "error": "run_id is required"}, ensure_ascii=False, indent=2)
    run = get_workflow_run(clean_run_id, include_steps=True)
    if not run:
        return json.dumps({"ok": False, "error": f"Unknown run_id: {clean_run_id}"}, ensure_ascii=False, indent=2)

    steps = [
        {
            "step_key": step["step_key"],
            "title": step["title"],
            "status": step["status"],
            "compensation": step.get("compensation", ""),
            "attempt_count": step.get("attempt_count", 0),
            "last_summary": step.get("last_summary", ""),
        }
        for step in reversed(run.get("steps") or [])
        if step.get("compensation")
        and step.get("status") in {"completed", "running", "retrying", "failed", "blocked", "waiting_approval"}
    ]
    replay = get_workflow_replay(clean_run_id, limit=max(1, int(checkpoint_limit or 10)))
    recent_compensations = [
        {
            "created_at": item["created_at"],
            "checkpoint_label": item["checkpoint_label"],
            "step_key": item["step_key"],
            "summary": item["summary"],
            "compensation_note": item.get("compensation_note", ""),
            "actor": item.get("actor", ""),
        }
        for item in replay
        if item.get("compensation_note")
    ]

    return json.dumps(
        {
            "ok": True,
            "run_id": clean_run_id,
            "status": run["status"],
            "owner": run.get("owner", ""),
            "current_step_key": run.get("current_step_key", ""),
            "compensation_steps": steps,
            "recent_compensation_notes": recent_compensations,
            "recommended_action": (
                "Execute compensation steps in listed order before cancelling or reopening the run."
                if steps
                else "No compensation steps registered for this workflow."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


def handle_workflow_resume(run_id: str) -> str:
    """Summarize what to do next for an active workflow run."""
    clean_run_id = (run_id or "").strip()
    if not clean_run_id:
        return json.dumps({"ok": False, "error": "run_id is required"}, ensure_ascii=False, indent=2)
    resume = get_workflow_resume_state(clean_run_id)
    if not resume:
        return json.dumps({"ok": False, "error": f"Unknown run_id: {clean_run_id}"}, ensure_ascii=False, indent=2)

    run = resume["run"]
    response = {
        "ok": True,
        "run_id": run["run_id"],
        "status": run["status"],
        "resume_state": resume["resume_state"],
        "can_resume": resume["can_resume"],
        "requires_approval": resume["requires_approval"],
        "message": resume["message"],
        "current_step_key": run.get("current_step_key", ""),
        "next_action": run.get("next_action", ""),
    }
    if resume.get("next_step"):
        response["next_step"] = {
            "step_key": resume["next_step"]["step_key"],
            "title": resume["next_step"]["title"],
            "status": resume["next_step"]["status"],
            "attempt_count": resume["next_step"].get("attempt_count", 0),
            "max_retries": resume["next_step"].get("max_retries", 0),
            "retry_after": resume["next_step"].get("retry_after", ""),
        }
    return json.dumps(response, ensure_ascii=False, indent=2)


def handle_workflow_replay(run_id: str, limit: int = 20) -> str:
    """Show the latest replayable checkpoints for a workflow run."""
    clean_run_id = (run_id or "").strip()
    if not clean_run_id:
        return json.dumps({"ok": False, "error": "run_id is required"}, ensure_ascii=False, indent=2)
    replay = get_workflow_replay(clean_run_id, limit=max(1, int(limit or 20)))
    if not replay:
        return json.dumps({"ok": False, "error": f"No replay data for {clean_run_id}"}, ensure_ascii=False, indent=2)

    items = []
    for checkpoint in replay:
        items.append(
            {
                "created_at": checkpoint["created_at"],
                "checkpoint_label": checkpoint["checkpoint_label"],
                "step_key": checkpoint["step_key"],
                "run_status": checkpoint["run_status"],
                "step_status": checkpoint["step_status"],
                "attempt": checkpoint["attempt"],
                "summary": checkpoint["summary"],
                "next_action": checkpoint["next_action"],
                "requires_approval": checkpoint["requires_approval"],
            }
        )
    return json.dumps(
        {"ok": True, "run_id": clean_run_id, "count": len(items), "checkpoints": items},
        ensure_ascii=False,
        indent=2,
    )


def handle_workflow_list(status: str = "", include_closed: bool = False, limit: int = 20) -> str:
    """List durable workflow runs so blocked/active work does not disappear into notes."""
    runs = list_workflow_runs(
        status=(status or "").strip().lower(),
        include_closed=bool(include_closed),
        limit=max(1, int(limit or 20)),
    )
    return json.dumps(
        {
            "ok": True,
            "count": len(runs),
            "runs": [
                {
                    "run_id": run["run_id"],
                    "session_id": run.get("session_id", ""),
                    "goal_id": run.get("goal_id", ""),
                    "goal": run["goal"],
                    "workflow_kind": run.get("workflow_kind", ""),
                    "status": run["status"],
                    "priority": run.get("priority", "normal"),
                    "current_step_key": run.get("current_step_key", ""),
                    "next_action": run.get("next_action", ""),
                    "updated_at": run["updated_at"],
                }
                for run in runs
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


TOOLS = [
    (handle_goal_open, "nexo_goal_open", "Open a durable goal so multi-session objectives stay explicit instead of dissolving into notes."),
    (handle_goal_update, "nexo_goal_update", "Update a durable goal with active/blocked/abandoned/completed state and next action."),
    (handle_goal_get, "nexo_goal_get", "Read a durable goal and optionally include linked workflow runs."),
    (handle_goal_list, "nexo_goal_list", "List durable goals so active, blocked, and abandoned objectives stay visible."),
    (handle_workflow_open, "nexo_workflow_open", "Open a durable workflow run for long multi-step or cross-session work."),
    (handle_workflow_update, "nexo_workflow_update", "Update a workflow run with replayable checkpoints, step status, retry metadata, and shared state."),
    (handle_workflow_get, "nexo_workflow_get", "Read the full durable workflow state, including shared_state, recent actors, and replayable checkpoints."),
    (handle_workflow_handoff, "nexo_workflow_handoff", "Record a durable handoff so another agent or client can resume the workflow honestly."),
    (handle_workflow_compensation, "nexo_workflow_compensation", "Show the rollback / compensation plan for a partially completed workflow run."),
    (handle_workflow_resume, "nexo_workflow_resume", "Summarize the next actionable step for a workflow run, including retry or approval gates."),
    (handle_workflow_replay, "nexo_workflow_replay", "Replay the latest checkpoints of a workflow run so interrupted execution can resume honestly."),
    (handle_workflow_list, "nexo_workflow_list", "List durable workflow runs so active, blocked, and resumable work stays visible."),
]
