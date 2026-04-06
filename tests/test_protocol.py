import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture(autouse=True)
def protocol_runtime(isolated_db):
    import db._core as db_core
    import db._protocol as db_protocol
    import db
    import plugins.protocol as protocol

    importlib.reload(db_core)
    importlib.reload(db_protocol)
    importlib.reload(db)
    importlib.reload(protocol)
    yield


def _register_session(sid: str):
    from db import register_session

    register_session(sid, "protocol test")
    return sid


def test_task_open_records_protocol_contract():
    from db import get_db
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-1001-2001")
    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal="Harden protocol discipline",
            task_type="edit",
            area="nexo-ops",
            files="/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/server.py",
            plan='["inspect", "patch", "test"]',
            evidence_refs='["spec", "repo inspection"]',
            verification_step="run pytest",
            context_hint="Implementing protocol discipline package",
        )
    )

    assert payload["ok"] is True
    assert payload["mode"] == "act"
    assert payload["contract"]["must_verify"] is True
    assert payload["contract"]["must_change_log"] is True
    row = get_db().execute(
        "SELECT * FROM protocol_tasks WHERE task_id = ?",
        (payload["task_id"],),
    ).fetchone()
    assert row is not None
    assert row["opened_with_guard"] == 1
    assert row["task_type"] == "edit"


def test_confidence_check_requires_verify_when_answer_has_no_evidence():
    from plugins.protocol import handle_confidence_check

    payload = json.loads(
        handle_confidence_check(
            goal="Answer whether the note mentions a feature flag",
            task_type="answer",
            context_hint="Need a reliable factual answer",
        )
    )

    assert payload["ok"] is True
    assert payload["mode"] == "verify"
    assert payload["confidence"] < 85
    assert "no evidence_refs supplied" in payload["reasons"]


def test_task_open_persists_defer_mode_for_high_stakes_answer():
    from db import get_db
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-1011-2011")
    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal="Confirm whether the production release should be launched now",
            task_type="answer",
            area="release",
            context_hint="User wants a yes/no launch answer for production",
            stakes="high",
        )
    )

    assert payload["ok"] is True
    assert payload["response_contract"]["mode"] == "defer"
    assert payload["contract"]["must_verify"] is False
    assert "Do not answer yet" in payload["next_action"]
    row = get_db().execute(
        "SELECT response_mode, response_confidence, response_high_stakes FROM protocol_tasks WHERE task_id = ?",
        (payload["task_id"],),
    ).fetchone()
    assert row["response_mode"] == "defer"
    assert row["response_confidence"] < 85
    assert row["response_high_stakes"] == 1


def test_task_open_with_blocking_guard_creates_guard_debt(monkeypatch):
    from db import get_db
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-1004-2004")
    monkeypatch.setattr(
        "plugins.protocol.handle_guard_check",
        lambda **kwargs: "BLOCKING RULES (resolve BEFORE writing):\n  #41 [FILE RULE:/tmp/x]: Read the canonical rule first\n",
    )
    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal="Edit guarded file",
            task_type="edit",
            area="nexo-ops",
            files="/tmp/x",
        )
    )

    assert payload["guard"]["has_blocking"] is True
    assert payload["guard"]["blocking_rule_ids"] == [41]
    debt = get_db().execute(
        "SELECT debt_type, status FROM protocol_debt WHERE task_id = ?",
        (payload["task_id"],),
    ).fetchone()
    assert debt["debt_type"] == "unacknowledged_guard_blocking"
    assert debt["status"] == "open"


def test_task_acknowledge_guard_resolves_guard_debt(monkeypatch):
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_acknowledge_guard

    sid = _register_session("nexo-1005-2005")
    monkeypatch.setattr(
        "plugins.protocol.handle_guard_check",
        lambda **kwargs: "BLOCKING RULES (resolve BEFORE writing):\n  #41 [FILE RULE:/tmp/x]: Read the canonical rule first\n",
    )
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Edit guarded file",
            task_type="edit",
            area="nexo-ops",
            files="/tmp/x",
        )
    )
    acknowledged = json.loads(
        handle_task_acknowledge_guard(
            sid=sid,
            task_id=opened["task_id"],
            learning_ids="41",
            note="Canonical file rule reviewed before edit.",
        )
    )

    assert acknowledged["ok"] is True
    assert acknowledged["acknowledged_rule_ids"] == [41]
    debt = get_db().execute(
        "SELECT status, resolution FROM protocol_debt WHERE task_id = ? AND debt_type = 'unacknowledged_guard_blocking'",
        (opened["task_id"],),
    ).fetchone()
    assert debt["status"] == "resolved"
    assert "Canonical file rule reviewed" in debt["resolution"]


def test_task_close_creates_change_log_and_stays_clean():
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1002-2002")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Patch runtime provider",
            task_type="edit",
            area="nexo-ops",
            files="/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/doctor/providers/runtime.py",
            plan='["inspect", "patch", "pytest"]',
            verification_step="run targeted pytest",
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="pytest -q tests/test_doctor.py passed",
            change_summary="Updated runtime protocol compliance to use live protocol data",
            change_why="Make doctor enforce discipline from live runtime data",
            change_verify="pytest -q tests/test_doctor.py",
        )
    )

    assert closed["ok"] is True
    assert closed["status"] == "clean"
    assert closed["change_log_id"] is not None
    row = get_db().execute(
        "SELECT * FROM protocol_tasks WHERE task_id = ?",
        (opened["task_id"],),
    ).fetchone()
    assert row["status"] == "done"
    assert row["change_log_id"] == closed["change_log_id"]


def test_task_close_opens_protocol_debt_when_done_without_evidence():
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Edit without evidence",
            task_type="edit",
            area="nexo-ops",
            files="/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/cortex.py",
            plan='["inspect", "patch"]',
            verification_step="run pytest",
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            change_summary="Touched cortex internals",
            change_why="Exercise missing-evidence debt path",
        )
    )

    assert closed["status"] == "debt-open"
    debt_types = {item["debt_type"] for item in closed["open_debts"]}
    assert "claimed_done_without_evidence" in debt_types
    count = get_db().execute(
        "SELECT COUNT(*) FROM protocol_debt WHERE task_id = ? AND debt_type = 'claimed_done_without_evidence' AND status = 'open'",
        (opened["task_id"],),
    ).fetchone()[0]
    assert count == 1


def test_task_close_auto_captures_learning_when_correction_has_no_learning():
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1004-2004")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Fix guard false positive",
            task_type="edit",
            area="nexo-ops",
            files="/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/guard.py",
            plan='["inspect", "patch", "test"]',
            verification_step="run pytest",
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="pytest -q tests/test_guard.py passed",
            correction_happened=True,
            change_summary="Reduced guard false positives",
            change_why="Capture missing-learning auto-learning path",
        )
    )

    assert closed["status"] == "clean"
    assert closed["learning_id"] is not None
    assert closed["followup_id"] == ""
    learning = get_db().execute(
        "SELECT title, applies_to, status FROM learnings WHERE id = ?",
        (closed["learning_id"],),
    ).fetchone()
    assert learning is not None
    assert learning["status"] == "active"
    assert learning["title"] == "Reduced guard false positives"
    assert "/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/guard.py" in learning["applies_to"]


def test_task_close_explicit_learning_supersedes_conflicting_file_rule():
    from db import create_learning, get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1006-2006")
    existing = create_learning(
        "nexo-ops",
        "Never edit guard.py directly",
        "Never edit guard.py directly; route all fixes through wrapper helpers instead.",
        applies_to="/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/guard.py",
        status="active",
    )
    get_db().execute(
        "UPDATE learnings SET priority = 'critical', weight = 1.0 WHERE id = ?",
        (existing["id"],),
    )
    get_db().commit()

    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Stabilize guard hotfix path",
            task_type="edit",
            area="nexo-ops",
            files="/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/guard.py",
            plan='["inspect", "patch", "test"]',
            verification_step="run pytest",
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="pytest -q tests/test_guard.py passed",
            correction_happened=True,
            change_summary="Guard hotfixes may edit guard.py directly if fully verified",
            change_why="Replace the older blanket prohibition with a tighter canonical rule.",
            learning_title="Guard hotfixes may edit guard.py directly if fully verified",
            learning_content="Edit guard.py directly for urgent hotfixes when the change is fully verified and the old blanket prohibition no longer matches reality.",
        )
    )

    assert closed["status"] == "clean"
    assert closed["learning_id"] is not None
    old_row = get_db().execute(
        "SELECT status FROM learnings WHERE id = ?",
        (existing["id"],),
    ).fetchone()
    new_row = get_db().execute(
        "SELECT supersedes_id, status, applies_to FROM learnings WHERE id = ?",
        (closed["learning_id"],),
    ).fetchone()
    assert old_row["status"] == "superseded"
    assert new_row["status"] == "active"
    assert new_row["supersedes_id"] == existing["id"]
    assert "/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/guard.py" in new_row["applies_to"]


def test_task_open_surfaces_attention_management_when_focus_is_split():
    from plugins.protocol import handle_task_open
    from plugins.workflow import handle_goal_open, handle_workflow_open

    sid = _register_session("nexo-1009-2009")
    goal_a = json.loads(handle_goal_open(sid=sid, title="Finish protocol discipline"))
    goal_b = json.loads(handle_goal_open(sid=sid, title="Finish workflow runtime"))
    json.loads(
        handle_workflow_open(
            sid=sid,
            goal="Continue protocol hardening",
            goal_id=goal_a["goal_id"],
            steps=json.dumps([{"step_key": "inspect", "title": "Inspect"}]),
        )
    )
    json.loads(
        handle_workflow_open(
            sid=sid,
            goal="Continue workflow hardening",
            goal_id=goal_b["goal_id"],
            steps=json.dumps([{"step_key": "patch", "title": "Patch"}]),
        )
    )
    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal="Open another execution task while focus is already split",
            task_type="execute",
            area="nexo",
        )
    )

    assert payload["ok"] is True
    assert payload["attention"]["status"] == "split"
    assert payload["attention"]["active_goals"] == 2
    assert "split across multiple active goals" in payload["attention"]["warnings"][0]


def test_task_open_previews_anticipatory_warnings_without_firing_trigger():
    import cognitive
    from plugins.protocol import handle_task_open
    from db import get_followup

    sid = _register_session("nexo-1010-2010")
    trigger_id = cognitive.create_trigger(
        "release",
        "Validate release readiness before claiming launch.",
        "Release tasks must pass doctor and evidence gates first.",
    )

    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal="Prepare the public release package",
            task_type="edit",
            area="nexo",
        )
    )
    triggers = cognitive.list_triggers("armed")

    assert payload["ok"] is True
    assert payload["anticipation"]["warning_count"] == 1
    assert payload["anticipation"]["warnings"][0]["action"] == "Validate release readiness before claiming launch."
    assert payload["preventive_followup"]["id"].startswith("NF-PROTOCOL-")
    assert get_followup(payload["preventive_followup"]["id"]) is not None
    assert any(trigger["id"] == trigger_id for trigger in triggers)
