import importlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture(autouse=True)
def portability_runtime(isolated_db, monkeypatch):
    import db._core as db_core
    import db._episodic as db_episodic
    import db
    import tools_sessions

    importlib.reload(db_core)
    importlib.reload(db_episodic)
    importlib.reload(db)
    importlib.reload(tools_sessions)
    portability_dir = Path(isolated_db["nexo_db"]).parent / "portability"
    # Override the lazy path helper so internal callers resolve to the
    # isolated sandbox. The former ``SESSION_PORTABILITY_DIR`` constant is
    # still exposed via PEP 562 __getattr__ for external callers.
    monkeypatch.setattr(tools_sessions, "_session_portability_dir", lambda: portability_dir)
    yield


def _seed_portable_session():
    from db import get_db, register_session, save_checkpoint

    sid = "nexo-2001-3001"
    register_session(
        sid,
        "Close v3 release",
        external_session_id="codex-ext-1",
        session_client="codex",
    )
    save_checkpoint(
        sid,
        task="Close v3 release",
        task_status="active",
        current_goal="Ship public v3 release",
        next_step="Run release checklist",
        active_files='["README.md","CHANGELOG.md"]',
    )
    conn = get_db()
    conn.execute(
        """INSERT INTO session_diary (
               session_id, decisions, discarded, pending, context_next, mental_state, summary, domain, user_signals, self_critique, source
           ) VALUES (?, ?, '', ?, ?, 'focused', ?, 'nexo', '', '', 'codex')""",
        (
            sid,
            "Keep v3 as one public release",
            "Finish release packaging",
            "Resume from release checklist and publish compare/docs",
            "Release work progressed and remaining work is release packaging.",
        ),
    )
    conn.execute(
        """INSERT INTO protocol_tasks (
               task_id, session_id, goal, task_type, area, status
           ) VALUES (?, ?, ?, 'edit', 'release', 'open')""",
        ("PT-1", sid, "Finalize changelog and release notes"),
    )
    conn.execute(
        """INSERT INTO workflow_goals (
               goal_id, session_id, title, status, priority, next_action
           ) VALUES (?, ?, ?, 'active', 'high', ?)""",
        ("WG-1", sid, "Ship v3 release", "Publish npm/github release"),
    )
    conn.execute(
        """INSERT INTO workflow_runs (
               run_id, session_id, goal_id, goal, workflow_kind, status, current_step_key, next_action
           ) VALUES (?, ?, ?, ?, 'release', 'running', 'release', ?)""",
        ("WR-1", sid, "WG-1", "Ship v3 release", "Publish artifacts"),
    )
    conn.commit()
    return sid


def test_session_portable_context_surfaces_checkpoint_and_open_work():
    from tools_sessions import handle_session_portable_context

    sid = _seed_portable_session()
    text = handle_session_portable_context(sid)

    assert "SESSION PORTABILITY PACKET" in text
    assert "SID: nexo-2001-3001" in text
    assert "Task: Close v3 release" in text
    assert "Goal: Ship public v3 release" in text
    assert "PT-1: Finalize changelog and release notes" in text
    assert "WG-1: Ship v3 release [active]" in text
    assert "WR-1: Ship v3 release [running]" in text


def test_session_export_bundle_writes_machine_readable_payload(tmp_path):
    from tools_sessions import handle_session_export_bundle

    sid = _seed_portable_session()
    export_path = tmp_path / "bundle.json"
    payload = json.loads(handle_session_export_bundle(sid, str(export_path)))

    assert payload["ok"] is True
    assert payload["sid"] == sid
    assert export_path.is_file()

    bundle = json.loads(export_path.read_text())
    assert bundle["session"]["sid"] == sid
    assert bundle["checkpoint"]["current_goal"] == "Ship public v3 release"
    assert bundle["open_protocol_tasks"][0]["task_id"] == "PT-1"
    assert bundle["open_workflow_goals"][0]["goal_id"] == "WG-1"
    assert bundle["open_workflow_runs"][0]["run_id"] == "WR-1"


def test_session_export_bundle_redacts_recent_context_body(tmp_path):
    from db import capture_context_event
    from tools_sessions import handle_session_export_bundle

    sid = _seed_portable_session()
    capture_context_event(
        session_id=sid,
        event_type="tool_output",
        title="Close v3 release body leak check",
        body="Raw body from /Users/franciscoc/private token=raw-secret-value",
        source_id="test",
    )
    export_path = tmp_path / "bundle.json"
    handle_session_export_bundle(sid, str(export_path))

    exported = export_path.read_text()
    assert "Raw body" not in exported
    assert "/Users/franciscoc" not in exported
    assert "raw-secret-value" not in exported
    assert "[redacted_payload]" in exported


def test_session_portable_context_redacts_recent_context_body():
    from db import capture_context_event
    from tools_sessions import handle_session_portable_context

    sid = _seed_portable_session()
    capture_context_event(
        session_id=sid,
        event_type="tool_output",
        title="Close v3 release portable leak check",
        body="Portable raw body from /Users/franciscoc/private token=raw-secret-value",
        source_id="test",
    )

    text = handle_session_portable_context(sid)
    assert "Portable raw body" not in text
    assert "/Users/franciscoc" not in text
    assert "raw-secret-value" not in text
    assert "[redacted_payload]" in text


def test_heartbeat_redacts_inbox_and_pending_questions():
    from db import ask_question, register_session, send_message
    from tools_sessions import handle_heartbeat

    sender = "nexo-3001-4001"
    target = "nexo-3001-4002"
    register_session(sender, "sender task", session_client="codex")
    register_session(target, "target task", session_client="codex")
    send_message(sender, target, "message /Users/franciscoc/private token=raw-secret-value 192.168.1.8")
    ask_question(sender, target, "question /Users/franciscoc/private token=raw-secret-value 192.168.1.8")

    text = handle_heartbeat(target, "target task", context_hint="")
    assert "/Users/franciscoc" not in text
    assert "raw-secret-value" not in text
    assert "192.168.1.8" not in text
    assert "[redacted_path]" in text
