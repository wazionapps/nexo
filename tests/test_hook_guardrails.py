"""Tests for post-tool conditioned file guardrails."""

from __future__ import annotations

import importlib
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
