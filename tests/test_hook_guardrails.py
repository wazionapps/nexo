"""Tests for post-tool conditioned file guardrails."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[1] / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_guardrail_stack():
    import db._core as db_core
    import db._fts as db_fts
    import db._schema as db_schema
    import db._learnings as db_learnings
    import db._protocol as db_protocol
    import db._sessions as db_sessions
    import db
    import hook_guardrails

    importlib.reload(db_core)
    importlib.reload(db_fts)
    importlib.reload(db_schema)
    importlib.reload(db_learnings)
    importlib.reload(db_protocol)
    importlib.reload(db_sessions)
    importlib.reload(db)
    importlib.reload(hook_guardrails)
    return db, hook_guardrails


@pytest.fixture
def guardrail_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "brain").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    return home


def test_process_tool_event_warns_and_records_debt_on_read_of_conditioned_file_without_protocol(guardrail_env):
    db, hook_guardrails = _reload_guardrail_stack()
    db.init_db()
    db.register_session(
        "nexo-2001-3001",
        "read conditioned file",
        external_session_id="claude-read-1",
        session_client="claude_code",
    )
    db.create_learning(
        "nexo-ops",
        "Read the rule before editing protocol.py",
        "Read the canonical protocol rule before any write step.",
        applies_to="/repo/src/plugins/protocol.py",
        status="active",
    )

    result = hook_guardrails.process_tool_event(
        {
            "session_id": "claude-read-1",
            "tool_name": "Read",
            "tool_input": {"file_path": "/repo/src/plugins/protocol.py"},
        }
    )

    assert result["status"] == "warn"
    assert result["warnings"]
    debt = db.get_db().execute(
        "SELECT debt_type, severity FROM protocol_debt WHERE debt_type = 'conditioned_file_read_without_protocol'"
    ).fetchone()
    assert debt["severity"] == "warn"


def test_process_tool_event_records_debt_when_writing_conditioned_file_without_protocol(guardrail_env):
    db, hook_guardrails = _reload_guardrail_stack()
    db.init_db()
    db.register_session(
        "nexo-2002-3002",
        "write conditioned file",
        external_session_id="claude-write-1",
        session_client="claude_code",
    )
    db.create_learning(
        "nexo-ops",
        "Protocol.py requires explicit task",
        "Edit protocol.py only through protocol tasks with evidence.",
        applies_to="/repo/src/plugins/protocol.py",
        status="active",
    )

    result = hook_guardrails.process_tool_event(
        {
            "session_id": "claude-write-1",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/repo/src/plugins/protocol.py"},
        }
    )

    assert result["status"] == "violation"
    assert result["violations"][0]["debt_type"] == "conditioned_file_touch_without_protocol"
    debt = db.get_db().execute(
        "SELECT debt_type, severity FROM protocol_debt WHERE debt_type = 'conditioned_file_touch_without_protocol'"
    ).fetchone()
    assert debt["severity"] == "error"


def test_process_tool_event_records_debt_when_writing_before_guard_ack(guardrail_env):
    db, hook_guardrails = _reload_guardrail_stack()
    db.init_db()
    sid = "nexo-2003-3003"
    db.register_session(
        sid,
        "write guarded file",
        external_session_id="claude-write-2",
        session_client="claude_code",
    )
    db.create_learning(
        "nexo-ops",
        "Guard file rule",
        "Never edit guard.py directly without reviewing the canonical rule.",
        applies_to="/repo/src/plugins/guard.py",
        status="active",
    )
    task = db.create_protocol_task(
        sid,
        "Patch guard file",
        task_type="edit",
        files=["/repo/src/plugins/guard.py"],
        opened_with_guard=True,
        guard_has_blocking=True,
        guard_summary="BLOCKING RULES (resolve BEFORE writing):\n  #41 [FILE RULE:/repo/src/plugins/guard.py]: Read the canonical rule first\n",
    )
    db.create_protocol_debt(
        sid,
        "unacknowledged_guard_blocking",
        severity="error",
        task_id=task["task_id"],
        evidence="Guard rule still unacknowledged for /repo/src/plugins/guard.py",
    )

    result = hook_guardrails.process_tool_event(
        {
            "session_id": "claude-write-2",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/repo/src/plugins/guard.py"},
        }
    )

    assert result["status"] == "violation"
    assert result["violations"][0]["debt_type"] == "conditioned_file_touch_without_guard_ack"
    debt = db.get_db().execute(
        "SELECT task_id, severity FROM protocol_debt WHERE debt_type = 'conditioned_file_touch_without_guard_ack'"
    ).fetchone()
    assert debt["task_id"] == task["task_id"]
    assert debt["severity"] == "error"


def test_process_tool_event_warns_on_non_trivial_work_without_task_open(guardrail_env):
    db, hook_guardrails = _reload_guardrail_stack()
    db.init_db()
    db.register_session(
        "nexo-2009-3009",
        "bash without task",
        external_session_id="claude-bash-1",
        session_client="claude_code",
    )

    result = hook_guardrails.process_tool_event(
        {
            "session_id": "claude-bash-1",
            "tool_name": "Bash",
            "tool_input": {"cmd": "rg -n protocol src"},
        }
    )

    assert result["status"] == "warn"
    messages = [item["message"] for item in result["warnings"]]
    assert any("nexo_task_open" in message for message in messages)
    assert any("nexo_workflow_open" in message for message in messages)
    assert any("nexo_task_close" in message for message in messages)


def test_process_tool_event_warns_when_multi_step_action_task_lacks_workflow(guardrail_env):
    db, hook_guardrails = _reload_guardrail_stack()
    db.init_db()
    sid = "nexo-2010-3010"
    db.register_session(
        sid,
        "edit without workflow",
        external_session_id="claude-edit-3",
        session_client="claude_code",
    )
    db.create_protocol_task(
        sid,
        "Implement multi-step fix",
        task_type="edit",
        files=["/repo/src/plugins/protocol.py"],
        plan=["inspect", "patch", "verify"],
        verification_step="pytest -q tests/test_protocol.py",
        opened_with_guard=True,
        must_verify=True,
        must_change_log=True,
    )

    result = hook_guardrails.process_tool_event(
        {
            "session_id": "claude-edit-3",
            "tool_name": "Bash",
            "tool_input": {"cmd": "pytest -q tests/test_protocol.py"},
        }
    )

    assert result["status"] == "warn"
    messages = [item["message"] for item in result["warnings"]]
    assert any("nexo_workflow_open" in message for message in messages)
    assert any("nexo_task_close" in message for message in messages)
    assert any("nexo_change_log" in message for message in messages)


def test_process_pre_tool_event_blocks_write_without_open_task_in_strict_mode(guardrail_env, monkeypatch):
    db, hook_guardrails = _reload_guardrail_stack()
    db.init_db()
    # v6.0.0 — strictness is no longer a configured preference; mock the
    # live helper the guardrail uses so the test still exercises the
    # "strict" code path deterministically.
    monkeypatch.setattr(hook_guardrails, "get_protocol_strictness", lambda: "strict")
    db.register_session(
        "nexo-2004-3004",
        "strict edit",
        external_session_id="claude-strict-1",
        session_client="claude_code",
    )

    result = hook_guardrails.process_pre_tool_event(
        {
            "session_id": "claude-strict-1",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/repo/src/plugins/protocol.py"},
        }
    )

    assert result["status"] == "blocked"
    assert result["strictness"] == "strict"
    assert result["blocks"][0]["debt_type"] == "strict_protocol_write_without_task"
    message = hook_guardrails.format_pretool_block_message(result)
    assert "open `nexo_task_open" in message


def test_process_pre_tool_event_learning_mode_explains_guard_ack_requirement(guardrail_env, monkeypatch):
    db, hook_guardrails = _reload_guardrail_stack()
    db.init_db()
    # v6.0.0 — force "learning" strictness directly on the guardrail.
    monkeypatch.setattr(hook_guardrails, "get_protocol_strictness", lambda: "learning")
    sid = "nexo-2005-3005"
    db.register_session(
        sid,
        "learning mode edit",
        external_session_id="claude-learning-1",
        session_client="claude_code",
    )
    task = db.create_protocol_task(
        sid,
        "Patch guard file",
        task_type="edit",
        files=["/repo/src/plugins/guard.py"],
        opened_with_guard=True,
        guard_has_blocking=True,
        guard_summary="BLOCKING RULES",
    )
    db.create_protocol_debt(
        sid,
        "unacknowledged_guard_blocking",
        severity="error",
        task_id=task["task_id"],
        evidence="Guard rule still unacknowledged for /repo/src/plugins/guard.py",
    )

    result = hook_guardrails.process_pre_tool_event(
        {
            "session_id": "claude-learning-1",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/repo/src/plugins/guard.py"},
        }
    )

    assert result["status"] == "blocked"
    assert result["strictness"] == "learning"
    assert result["blocks"][0]["debt_type"] == "strict_protocol_write_without_guard_ack"
    message = hook_guardrails.format_pretool_block_message(result)
    assert "nexo_task_acknowledge_guard" in message


def test_process_pre_tool_event_blocks_automation_write_to_live_repo(guardrail_env, monkeypatch):
    db, hook_guardrails = _reload_guardrail_stack()
    db.init_db()
    monkeypatch.setenv("NEXO_AUTOMATION", "1")
    monkeypatch.delenv("NEXO_PUBLIC_CONTRIBUTION", raising=False)
    db.register_session(
        "nexo-2006-3006",
        "automation live repo edit",
        external_session_id="claude-auto-1",
        session_client="claude_code",
    )

    result = hook_guardrails.process_pre_tool_event(
        {
            "session_id": "claude-auto-1",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(REPO_SRC / "server.py")},
        }
    )

    assert result["status"] == "blocked"
    assert result["blocks"][0]["debt_type"] == "automation_live_repo_write_blocked"
    message = hook_guardrails.format_pretool_block_message(result)
    assert "isolated checkout/worktree" in message
    debt = db.get_db().execute(
        "SELECT debt_type, severity FROM protocol_debt WHERE debt_type = 'automation_live_repo_write_blocked'"
    ).fetchone()
    assert debt["severity"] == "error"


def test_process_pre_tool_event_allows_public_contribution_checkout(guardrail_env, monkeypatch):
    db, hook_guardrails = _reload_guardrail_stack()
    db.init_db()
    monkeypatch.setenv("NEXO_AUTOMATION", "1")
    monkeypatch.setenv("NEXO_PUBLIC_CONTRIBUTION", "1")
    db.register_session(
        "nexo-2007-3007",
        "public contribution edit",
        external_session_id="claude-auto-2",
        session_client="claude_code",
    )

    result = hook_guardrails.process_pre_tool_event(
        {
            "session_id": "claude-auto-2",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(REPO_SRC / "server.py")},
        }
    )

    assert result["skipped"] is True
    assert result["reason"] == "lenient mode"
    debt = db.get_db().execute(
        "SELECT COUNT(*) AS count FROM protocol_debt WHERE debt_type = 'automation_live_repo_write_blocked'"
    ).fetchone()
    assert debt["count"] == 0


def test_process_pre_tool_event_does_not_treat_runtime_home_as_live_repo_when_not_git_checkout(
    guardrail_env,
    monkeypatch,
):
    runtime_file = guardrail_env / "operations" / "orchestrator-state.json"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("{}")
    (guardrail_env / ".git").write_text("gitdir: /tmp/fake-git\n")

    monkeypatch.setenv("NEXO_CODE", str(guardrail_env))
    monkeypatch.setenv("NEXO_AUTOMATION", "1")
    monkeypatch.delenv("NEXO_PUBLIC_CONTRIBUTION", raising=False)
    db, hook_guardrails = _reload_guardrail_stack()
    db.init_db()
    db.register_session(
        "nexo-2008-3008",
        "automation runtime write",
        external_session_id="claude-auto-3",
        session_client="claude_code",
    )

    result = hook_guardrails.process_pre_tool_event(
        {
            "session_id": "claude-auto-3",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(runtime_file)},
        }
    )

    assert result["skipped"] is True
    assert result["reason"] == "lenient mode"
    debt = db.get_db().execute(
        "SELECT COUNT(*) AS count FROM protocol_debt WHERE debt_type = 'automation_live_repo_write_blocked'"
    ).fetchone()
    assert debt["count"] == 0
