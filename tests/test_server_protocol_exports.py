from __future__ import annotations

import asyncio
import sys
from pathlib import Path


REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def test_server_exposes_protocol_runtime_tools(isolated_db):
    import server

    expected = (
        "nexo_cortex_check",
        "nexo_continuity_snapshot_write",
        "nexo_continuity_snapshot_read",
        "nexo_continuity_resume_bundle",
        "nexo_continuity_compaction_event",
        "nexo_continuity_audit",
        "nexo_guard_check",
        "nexo_task_open",
        "nexo_task_acknowledge_guard",
        "nexo_task_close",
        "nexo_goal_open",
        "nexo_goal_update",
        "nexo_goal_get",
        "nexo_goal_list",
        "nexo_workflow_open",
        "nexo_workflow_update",
        "nexo_workflow_get",
        "nexo_workflow_handoff",
        "nexo_workflow_compensation",
        "nexo_workflow_resume",
        "nexo_workflow_replay",
        "nexo_workflow_list",
        "nexo_memory_event_list",
        "nexo_memory_event_stats",
        "nexo_memory_observation_process",
        "nexo_memory_observation_list",
        "nexo_memory_observation_stats",
        "nexo_memory_backfill",
        "nexo_memory_health",
        "nexo_memory_maintenance",
        "nexo_memory_search",
        "nexo_memory_answer",
        "nexo_memory_timeline",
        "nexo_pre_answer_route",
        "nexo_evidence_search",
        "nexo_evidence_record",
        "nexo_saved_not_used_audit",
        "nexo_automation_supervisor",
    )
    for name in expected:
        assert hasattr(server, name), f"{name} missing from server.py"
        assert callable(getattr(server, name)), f"{name} is not callable"


def test_memory_tools_are_registered_with_fastmcp(isolated_db):
    import server

    tools = asyncio.run(server.mcp.list_tools())
    names = {tool.name for tool in tools}

    for name in (
        "nexo_memory_event_list",
        "nexo_memory_event_stats",
        "nexo_memory_observation_process",
        "nexo_memory_observation_list",
        "nexo_memory_observation_stats",
        "nexo_memory_backfill",
        "nexo_memory_health",
        "nexo_memory_maintenance",
        "nexo_memory_search",
        "nexo_memory_answer",
        "nexo_memory_timeline",
    ):
        assert name in names

    assert "nexo_pre_answer_route" in names
    assert "nexo_evidence_search" in names
    assert "nexo_evidence_record" in names
    assert "nexo_saved_not_used_audit" in names
    assert "nexo_automation_supervisor" in names
    for name in (
        "nexo_goal_open",
        "nexo_goal_update",
        "nexo_goal_get",
        "nexo_goal_list",
        "nexo_workflow_get",
        "nexo_workflow_handoff",
        "nexo_workflow_compensation",
        "nexo_workflow_resume",
        "nexo_workflow_replay",
        "nexo_workflow_list",
    ):
        assert name in names
