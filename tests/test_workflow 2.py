import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture(autouse=True)
def workflow_runtime(isolated_db):
    import db._core as db_core
    import db._protocol as db_protocol
    import db._workflow as db_workflow
    import db
    import plugins.protocol as protocol
    import plugins.workflow as workflow

    importlib.reload(db_core)
    importlib.reload(db_protocol)
    importlib.reload(db_workflow)
    importlib.reload(db)
    importlib.reload(protocol)
    importlib.reload(workflow)
    yield


def _register_session(sid: str):
    from db import register_session

    register_session(sid, "workflow test")
    return sid


def test_workflow_open_records_steps_and_reuses_idempotency_key():
    from plugins.workflow import handle_workflow_open

    sid = _register_session("nexo-1101-2101")
    steps = json.dumps(
        [
            {"step_key": "inspect", "title": "Inspect state"},
            {"step_key": "patch", "title": "Patch runtime", "max_retries": 2},
        ]
    )
    first = json.loads(
        handle_workflow_open(
            sid=sid,
            goal="Ship durable workflow runtime",
            workflow_kind="runtime-hardening",
            idempotency_key="durable-runtime-v1",
            steps=steps,
            next_action="Start with inspect",
        )
    )
    second = json.loads(
        handle_workflow_open(
            sid=sid,
            goal="Ship durable workflow runtime",
            workflow_kind="runtime-hardening",
            idempotency_key="durable-runtime-v1",
            steps=steps,
        )
    )

    assert first["ok"] is True
    assert first["step_count"] == 2
    assert first["current_step_key"] == "inspect"
    assert second["ok"] is True
    assert second["reused_existing"] is True
    assert second["run_id"] == first["run_id"]


def test_workflow_open_rejects_foreign_protocol_task():
    from plugins.protocol import handle_task_open
    from plugins.workflow import handle_workflow_open

    sid_a = _register_session("nexo-1102-2102")
    sid_b = _register_session("nexo-1103-2103")
    task = json.loads(
        handle_task_open(
            sid=sid_a,
            goal="Long refactor task",
            task_type="edit",
            area="nexo",
            files="/tmp/runtime.py",
        )
    )

    result = json.loads(
        handle_workflow_open(
            sid=sid_b,
            goal="Attach foreign task",
            protocol_task_id=task["task_id"],
        )
    )

    assert result["ok"] is False
    assert "belongs to" in result["error"]


def test_workflow_waiting_approval_is_resumable_honestly():
    from plugins.workflow import handle_workflow_open, handle_workflow_replay, handle_workflow_resume, handle_workflow_update

    sid = _register_session("nexo-1104-2104")
    opened = json.loads(
        handle_workflow_open(
            sid=sid,
            goal="Roll out guarded migration",
            steps=json.dumps(
                [
                    {"step_key": "inspect", "title": "Inspect current schema"},
                    {"step_key": "approve", "title": "Wait for approval", "requires_approval": True},
                ]
            ),
        )
    )
    run_id = opened["run_id"]

    running = json.loads(
        handle_workflow_update(
            run_id=run_id,
            step_key="inspect",
            step_status="running",
            checkpoint_label="inspect-started",
            summary="Schema inspection in progress",
        )
    )
    completed = json.loads(
        handle_workflow_update(
            run_id=run_id,
            step_key="inspect",
            step_status="completed",
            checkpoint_label="inspect-done",
            summary="Schema inspection completed",
            next_action="Move to approval gate",
        )
    )
    approval = json.loads(
        handle_workflow_update(
            run_id=run_id,
            step_key="approve",
            step_status="waiting_approval",
            checkpoint_label="approval-gate",
            summary="Approval required before applying the migration",
            requires_approval=True,
            next_action="Wait for explicit approval",
        )
    )
    resume = json.loads(handle_workflow_resume(run_id))
    replay = json.loads(handle_workflow_replay(run_id, limit=4))

    assert running["ok"] is True
    assert completed["status"] == "running"
    assert approval["status"] == "waiting_approval"
    assert approval["resume_state"] == "waiting_approval"
    assert resume["can_resume"] is False
    assert resume["requires_approval"] is True
    assert resume["next_step"]["step_key"] == "approve"
    assert replay["ok"] is True
    assert replay["checkpoints"][0]["checkpoint_label"] == "approval-gate"


def test_workflow_retry_path_surfaces_resume_without_restarting_from_zero():
    from plugins.workflow import handle_workflow_open, handle_workflow_resume, handle_workflow_update

    sid = _register_session("nexo-1105-2105")
    opened = json.loads(
        handle_workflow_open(
            sid=sid,
            goal="Retry flaky deployment step",
            steps=json.dumps(
                [
                    {"step_key": "deploy", "title": "Deploy release", "max_retries": 2, "retry_policy": "exponential"},
                ]
            ),
        )
    )
    run_id = opened["run_id"]

    json.loads(
        handle_workflow_update(
            run_id=run_id,
            step_key="deploy",
            step_status="running",
            checkpoint_label="deploy-attempt-1",
            summary="First deploy attempt started",
        )
    )
    failed = json.loads(
        handle_workflow_update(
            run_id=run_id,
            step_key="deploy",
            step_status="failed",
            checkpoint_label="deploy-failed",
            summary="SSH timeout during deploy",
            retry_after="2026-04-06 12:00:00",
            max_retries=2,
            retry_policy="exponential",
            next_action="Retry deploy after inspecting SSH path",
        )
    )
    resume = json.loads(handle_workflow_resume(run_id))
    retried = json.loads(
        handle_workflow_update(
            run_id=run_id,
            step_key="deploy",
            step_status="retrying",
            checkpoint_label="deploy-attempt-2",
            summary="Retry deploy after SSH fix",
        )
    )

    assert failed["status"] == "failed"
    assert resume["resume_state"] == "retry_available"
    assert resume["can_resume"] is True
    assert resume["next_step"]["attempt_count"] == 1
    assert retried["resume_state"] in {"running", "retrying"}
    assert retried["next_step"]["attempt_count"] == 2


def test_workflow_list_hides_closed_runs_by_default():
    from plugins.workflow import handle_workflow_list, handle_workflow_open, handle_workflow_update

    sid = _register_session("nexo-1106-2106")
    opened = json.loads(
        handle_workflow_open(
            sid=sid,
            goal="One-shot complete workflow",
            steps=json.dumps([{"step_key": "done", "title": "Finish"}]),
        )
    )
    run_id = opened["run_id"]
    json.loads(
        handle_workflow_update(
            run_id=run_id,
            step_key="done",
            step_status="completed",
            checkpoint_label="done",
            summary="Completed the only step",
        )
    )

    active = json.loads(handle_workflow_list())
    all_runs = json.loads(handle_workflow_list(include_closed=True))

    assert all(item["run_id"] != run_id for item in active["runs"])
    assert any(item["run_id"] == run_id and item["status"] == "completed" for item in all_runs["runs"])


def test_goal_stack_links_workflows_and_survives_listing():
    from plugins.workflow import (
        handle_goal_get,
        handle_goal_list,
        handle_goal_open,
        handle_workflow_open,
    )

    sid = _register_session("nexo-1107-2107")
    goal = json.loads(
        handle_goal_open(
            sid=sid,
            title="Ship v3.0 goal stack",
            objective="Keep the durable objective visible across sessions",
            next_action="Link the first workflow run",
        )
    )
    run = json.loads(
        handle_workflow_open(
            sid=sid,
            goal="Implement durable goal stack runtime",
            goal_id=goal["goal_id"],
            steps=json.dumps([{"step_key": "schema", "title": "Add migration"}]),
        )
    )
    goal_full = json.loads(handle_goal_get(goal["goal_id"], include_runs=True))
    listed = json.loads(handle_goal_list())

    assert goal["ok"] is True
    assert run["ok"] is True
    assert run["goal_id"] == goal["goal_id"]
    assert goal_full["goal"]["run_count"] == 1
    assert goal_full["goal"]["open_run_count"] == 1
    assert goal_full["goal"]["runs"][0]["run_id"] == run["run_id"]
    assert any(item["goal_id"] == goal["goal_id"] for item in listed["goals"])


def test_workflow_get_and_handoff_preserve_shared_state_for_collaboration():
    from plugins.workflow import (
        handle_workflow_get,
        handle_workflow_handoff,
        handle_workflow_open,
    )

    sid = _register_session("nexo-1108-2108")
    opened = json.loads(
        handle_workflow_open(
            sid=sid,
            goal="Coordinate release readiness across agents",
            owner="codex",
            shared_state=json.dumps({"phase": "inspect", "owner_notes": ["doctor first"]}),
            steps=json.dumps([{"step_key": "inspect", "title": "Inspect runtime"}]),
        )
    )
    handed_off = json.loads(
        handle_workflow_handoff(
            run_id=opened["run_id"],
            actor="codex",
            new_owner="claude",
            next_action="Claude should verify doctor and changelog parity.",
            handoff_note="Runtime inspection finished; hand off release verification.",
            shared_state=json.dumps({"phase": "handoff", "doctor_ready": False}),
        )
    )
    fetched = json.loads(handle_workflow_get(opened["run_id"], include_steps=True, checkpoint_limit=4))

    assert handed_off["ok"] is True
    assert handed_off["owner"] == "claude"
    assert fetched["ok"] is True
    assert fetched["run"]["owner"] == "claude"
    assert fetched["run"]["shared_state"]["phase"] == "handoff"
    assert "codex" in fetched["recent_actors"]
    assert fetched["checkpoints"][0]["checkpoint_label"] == "handoff"


def test_workflow_compensation_surfaces_reverse_rollback_plan():
    from plugins.workflow import (
        handle_workflow_compensation,
        handle_workflow_open,
        handle_workflow_update,
    )

    sid = _register_session("nexo-1109-2109")
    opened = json.loads(
        handle_workflow_open(
            sid=sid,
            goal="Ship rollback-aware release workflow",
            steps=json.dumps(
                [
                    {
                        "step_key": "backup",
                        "title": "Backup state",
                        "compensation": "Restore the previous backup snapshot.",
                    },
                    {
                        "step_key": "deploy",
                        "title": "Deploy release",
                        "compensation": "Roll back to the last stable release artifact.",
                    },
                ]
            ),
        )
    )
    run_id = opened["run_id"]
    json.loads(
        handle_workflow_update(
            run_id=run_id,
            step_key="backup",
            step_status="completed",
            checkpoint_label="backup-done",
            summary="Backup completed",
        )
    )
    json.loads(
        handle_workflow_update(
            run_id=run_id,
            step_key="deploy",
            step_status="failed",
            checkpoint_label="deploy-failed",
            summary="Deploy failed after partial rollout",
            compensation="Roll back to the last stable release artifact.",
            actor="codex",
        )
    )
    plan = json.loads(handle_workflow_compensation(run_id))

    assert plan["ok"] is True
    assert plan["compensation_steps"][0]["step_key"] == "deploy"
    assert plan["compensation_steps"][1]["step_key"] == "backup"
    assert "Execute compensation steps" in plan["recommended_action"]


def test_goal_stack_tracks_blocked_and_hides_abandoned_by_default():
    from plugins.workflow import handle_goal_list, handle_goal_open, handle_goal_update

    sid = _register_session("nexo-1108-2108")
    goal = json.loads(handle_goal_open(sid=sid, title="Unblock release train"))
    blocked = json.loads(
        handle_goal_update(
            goal_id=goal["goal_id"],
            status="blocked",
            blocker_reason="Awaiting upstream review",
            next_action="Resume after PR review lands",
        )
    )
    active = json.loads(handle_goal_list())
    abandoned = json.loads(handle_goal_update(goal_id=goal["goal_id"], status="abandoned"))
    default_list = json.loads(handle_goal_list())
    include_closed = json.loads(handle_goal_list(include_closed=True))

    assert blocked["ok"] is True
    assert blocked["status"] == "blocked"
    assert blocked["blocker_reason"] == "Awaiting upstream review"
    assert any(item["goal_id"] == goal["goal_id"] and item["status"] == "blocked" for item in active["goals"])
    assert abandoned["status"] == "abandoned"
    assert all(item["goal_id"] != goal["goal_id"] for item in default_list["goals"])
    assert any(item["goal_id"] == goal["goal_id"] and item["status"] == "abandoned" for item in include_closed["goals"])
