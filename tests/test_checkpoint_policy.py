import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture
def checkpoint_runtime(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))

    import checkpoint_policy
    import db._core as db_core
    import db._protocol as db_protocol
    import db._workflow as db_workflow
    import db
    import plugins.protocol as protocol
    import plugins.workflow as workflow
    import tools_sessions

    importlib.reload(checkpoint_policy)
    importlib.reload(db_core)
    importlib.reload(db_protocol)
    importlib.reload(db_workflow)
    importlib.reload(db)
    importlib.reload(protocol)
    importlib.reload(workflow)
    importlib.reload(tools_sessions)
    monkeypatch.setattr("plugins.workflow.get_protocol_strictness", lambda: "lenient")
    yield checkpoint_policy


def _register_session(sid: str):
    from db import register_session

    register_session(sid, "checkpoint policy test")
    return sid


def test_record_milestone_flushes_every_third_milestone(checkpoint_runtime):
    import db

    cp = checkpoint_runtime
    sid = _register_session("nexo-1301-2301")

    first = cp.record_milestone(
        sid,
        reason="task-close:inspect",
        task="Ship continuity fix",
        active_files=["/tmp/a.py"],
        current_goal="Keep durable continuity",
        next_step="Patch heartbeat",
    )
    second = cp.record_milestone(
        sid,
        reason="workflow:patch",
        task="Ship continuity fix",
        active_files=["/tmp/b.py"],
        current_goal="Keep durable continuity",
        next_step="Run tests",
    )
    third = cp.record_milestone(
        sid,
        reason="workflow:test",
        task="Ship continuity fix",
        active_files=["/tmp/c.py"],
        current_goal="Keep durable continuity",
        decisions_summary="Patched heartbeat + hooks",
        blockers="",
        next_step="Prepare release notes",
    )

    assert first["checkpoint_written"] is False
    assert second["checkpoint_written"] is False
    assert third["checkpoint_written"] is True

    checkpoint = db.read_checkpoint(sid)
    assert checkpoint is not None
    assert checkpoint["current_goal"] == "Keep durable continuity"
    assert "/tmp/c.py" in checkpoint["active_files"]
    assert "checkpoint_reason=workflow:test" in checkpoint["decisions_summary"]
    assert checkpoint["next_step"] == "Prepare release notes"


def test_force_runtime_checkpoint_enriches_from_latest_workflow(checkpoint_runtime):
    import db
    from plugins.workflow import handle_workflow_open, handle_workflow_update

    cp = checkpoint_runtime
    sid = _register_session("nexo-1302-2302")

    opened = json.loads(
        handle_workflow_open(
            sid=sid,
            goal="Fix compaction continuity",
            steps=json.dumps([{"step_key": "inspect", "title": "Inspect"}]),
            next_action="Inspect current state",
        )
    )
    json.loads(
        handle_workflow_update(
            run_id=opened["run_id"],
            step_key="inspect",
            step_status="blocked",
            checkpoint_label="inspect-blocked",
            summary="Waiting for next action after partial inspection",
            next_action="Read the latest checkpoint after compaction",
        )
    )

    flushed = cp.force_runtime_checkpoint(sid, reason="pre-compact-hook")
    checkpoint = db.read_checkpoint(sid)

    assert flushed["ok"] is True
    assert flushed["checkpoint_written"] is True
    assert checkpoint is not None
    assert checkpoint["current_goal"] == "Fix compaction continuity"
    assert "Workflow blocked" in checkpoint["errors_found"]
    assert checkpoint["next_step"] == "Read the latest checkpoint after compaction"


def test_task_close_response_includes_durable_checkpoint(checkpoint_runtime):
    from plugins.protocol import handle_task_close, handle_task_open

    sid = _register_session("nexo-1303-2303")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Ship protocol fix",
            task_type="edit",
            area="nexo",
            files="/tmp/protocol.py",
            verification_step="run focused tests",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Focused protocol tests passed and checkpoint wiring was verified.",
            files_changed='["/tmp/protocol.py"]',
            change_summary="Added durable checkpoint wiring on task_close",
        )
    )

    assert closed["ok"] is True
    assert "durable_checkpoint" in closed
    assert closed["durable_checkpoint"]["checkpoint_written"] is False


def test_workflow_update_response_includes_durable_checkpoint(checkpoint_runtime):
    from plugins.workflow import handle_workflow_open, handle_workflow_update

    sid = _register_session("nexo-1304-2304")
    opened = json.loads(
        handle_workflow_open(
            sid=sid,
            goal="Keep execute-until-blocker through compaction",
            steps=json.dumps([{"step_key": "patch", "title": "Patch"}]),
        )
    )

    updated = json.loads(
        handle_workflow_update(
            run_id=opened["run_id"],
            step_key="patch",
            step_status="running",
            checkpoint_label="patch-started",
            summary="Patching heartbeat and post-compact surfaces",
            state_patch=json.dumps({"active_files": ["/tmp/heartbeat.py", "/tmp/post-compact.sh"]}),
            next_action="Finish the patch and run tests",
        )
    )

    assert updated["ok"] is True
    assert "durable_checkpoint" in updated
    assert updated["durable_checkpoint"]["pending_reason"] == "workflow:patch"
