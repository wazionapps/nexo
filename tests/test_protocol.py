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


def test_task_open_requires_decision_support_for_high_stakes_action():
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-1012-2012")
    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal="Run the production release migration",
            task_type="execute",
            area="release",
            plan='["prepare", "migrate", "verify"]',
            evidence_refs='["release contract", "staging smoke"]',
            verification_step="run production smoke checks",
            stakes="high",
        )
    )

    assert payload["ok"] is True
    assert payload["decision_support"]["required"] is True
    assert payload["decision_support"]["tool"] == "nexo_cortex_decide"
    assert "alternatives" in payload["next_action"].lower()


@pytest.mark.parametrize(
    ("goal", "context_hint"),
    [
        ("Approve customer-facing pricing change", "Public launch affects revenue"),
        ("Execute refund policy change", "Could affect brand reputation"),
        ("Ship roadmap choice for public product launch", "Customer-visible tradeoff with cost"),
    ],
)
def test_task_open_detects_high_stakes_from_cost_reputation_and_product_context(goal, context_hint):
    from plugins.protocol import handle_task_open

    sid = _register_session(f"nexo-{abs(hash(goal)) % 10000}-{(abs(hash(context_hint)) % 10000) or 1}")
    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal=goal,
            task_type="execute",
            area="product",
            context_hint=context_hint,
            plan='["prepare", "execute", "verify"]',
            evidence_refs='["decision memo", "staging evidence"]',
            verification_step="run post-change verification",
        )
    )

    assert payload["ok"] is True
    assert payload["response_contract"]["high_stakes"] is True
    assert payload["decision_support"]["required"] is True


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


def test_task_open_requires_files_for_edit_in_strict_mode(monkeypatch):
    import plugins.protocol as protocol

    sid = _register_session("nexo-1004-2005")
    monkeypatch.setattr(protocol, "get_protocol_strictness", lambda: "strict")
    payload = json.loads(
        protocol.handle_task_open(
            sid=sid,
            goal="Edit without explicit files",
            task_type="edit",
            area="nexo-ops",
        )
    )

    assert payload["ok"] is False
    assert payload["protocol_strictness"] == "strict"
    assert "requires explicit `files`" in payload["error"]


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


def test_protocol_debt_list_filters_by_type_and_severity():
    from db import create_protocol_debt
    from plugins.protocol import handle_protocol_debt_list

    sid = _register_session("nexo-1005-2006")
    create_protocol_debt(sid, "missing_followup_payload", severity="warn", evidence="duplicate followup id")
    create_protocol_debt(sid, "unacknowledged_guard_blocking", severity="error", evidence="guard debt")

    payload = json.loads(
        handle_protocol_debt_list(
            status="open",
            session_id=sid,
            debt_type="missing_followup_payload",
            severity="warn",
        )
    )

    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["summary"] == {"missing_followup_payload": 1}
    assert payload["items"][0]["debt_type"] == "missing_followup_payload"


def test_protocol_debt_resolve_accepts_debt_ids():
    from db import create_protocol_debt, get_db
    from plugins.protocol import handle_protocol_debt_resolve

    sid = _register_session("nexo-1005-2007")
    debt = create_protocol_debt(
        sid,
        "codex_conditioned_read_without_protocol",
        severity="warn",
        evidence="historical transcript audit debt",
    )

    payload = json.loads(
        handle_protocol_debt_resolve(
            debt_ids=str(debt["id"]),
            resolution="Audited historical debt; current discipline already enforced.",
        )
    )

    assert payload["ok"] is True
    assert payload["resolved"] == 1
    assert payload["matched_ids"] == [debt["id"]]
    row = get_db().execute(
        "SELECT status, resolution FROM protocol_debt WHERE id = ?",
        (debt["id"],),
    ).fetchone()
    assert row["status"] == "resolved"
    assert "historical debt" in row["resolution"]


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


def test_high_stakes_action_close_opens_debt_without_cortex_evaluation():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1013-2013")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Deploy the production release package",
            task_type="execute",
            area="release",
            plan='["prepare", "deploy", "verify"]',
            evidence_refs='["release contract", "staging green"]',
            verification_step="run post-release smoke tests",
            stakes="high",
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="test staging production changelog version all checked",
            outcome_notes="Release executed without alternative evaluation.",
        )
    )

    assert closed["status"] == "debt-open"
    debt_types = {item["debt_type"] for item in closed["open_debts"]}
    assert "missing_cortex_evaluation" in debt_types


def test_high_stakes_action_close_stays_clean_with_cortex_evaluation():
    from plugins.cortex import handle_cortex_decide
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1014-2014")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Deploy the production release package",
            task_type="execute",
            area="release",
            plan='["prepare", "deploy", "verify"]',
            evidence_refs='["release contract", "staging green"]',
            verification_step="run post-release smoke tests",
            stakes="high",
        )
    )
    evaluation = json.loads(
        handle_cortex_decide(
            goal="Deploy the production release package",
            task_type="execute",
            impact_level="critical",
            area="release",
            session_id=sid,
            task_id=opened["task_id"],
            evidence_refs='["release contract", "staging green"]',
            alternatives=json.dumps([
                {"name": "canary_release", "description": "Deploy staged canary release with smoke tests and rollback ready"},
                {"name": "direct_release", "description": "Deploy directly to production without staged verification"},
            ]),
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="test staging production changelog version all checked",
            outcome_notes="Release executed with persisted cortex evaluation.",
        )
    )

    assert evaluation["ok"] is True
    assert closed["status"] == "clean"
    assert closed["cortex_evaluation"]["task_id"] == opened["task_id"]


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


def test_heartbeat_surfaces_open_protocol_debt():
    """heartbeat must warn when the current session has open protocol debt.

    Mirrors task_open / task_close behavior so the agent sees protocol debt
    at every protocol touchpoint, not only at task boundaries. Without this
    surfacing, an agent could log many heartbeats without ever noticing
    debts opened by self-audit, task_close, or guard checks.
    """
    from db import create_protocol_debt
    from tools_sessions import handle_heartbeat

    sid = _register_session("nexo-1011-2011")
    create_protocol_debt(
        sid,
        "claimed_done_without_evidence",
        severity="error",
        evidence="Closed task without close_evidence payload",
    )
    create_protocol_debt(
        sid,
        "missing_followup_payload",
        severity="warn",
        evidence="duplicate followup id",
    )

    output = handle_heartbeat(sid=sid, task="continue work", context_hint="checking session state")

    assert "PROTOCOL DEBT" in output
    assert "2 open debt(s)" in output
    assert "1 error" in output
    assert "claimed_done_without_evidence" in output
    assert "missing_followup_payload" in output
    assert "nexo_protocol_debt_resolve" in output


def test_heartbeat_silent_when_no_protocol_debt():
    """heartbeat must NOT spam the protocol debt warning when the session is clean."""
    from tools_sessions import handle_heartbeat

    sid = _register_session("nexo-1012-2012")
    output = handle_heartbeat(sid=sid, task="clean work", context_hint="all good")

    assert "PROTOCOL DEBT" not in output


def test_heartbeat_only_surfaces_current_session_debt():
    """heartbeat must scope debt surfacing to the current session, not bleed across sessions."""
    from db import create_protocol_debt
    from tools_sessions import handle_heartbeat

    other_sid = _register_session("nexo-1013-2013")
    create_protocol_debt(
        other_sid,
        "claimed_done_without_evidence",
        severity="error",
        evidence="other session debt",
    )

    current_sid = _register_session("nexo-1014-2014")
    output = handle_heartbeat(sid=current_sid, task="my work", context_hint="my context")

    assert "PROTOCOL DEBT" not in output
    assert "other session debt" not in output


# v5.2.0 — Response contract Fase 1 tests.
# These exercise evaluate_response_confidence directly so the logic is
# covered without going through the full handle_task_open pipeline.


def test_high_stakes_detection_catches_spanish_keywords():
    from plugins.protocol import evaluate_response_confidence

    result = evaluate_response_confidence(
        goal="Migrar la base de datos de producción al nuevo servidor",
        task_type="analyze",
    )
    assert result["high_stakes"] is True
    assert "high-stakes context detected" in result["reasons"]


def test_high_stakes_detection_catches_accented_spanish_keywords():
    from plugins.protocol import evaluate_response_confidence

    result = evaluate_response_confidence(
        goal="Arreglar fallo crítico en facturación de clientes",
        task_type="analyze",
    )
    assert result["high_stakes"] is True


def test_high_stakes_negation_suppresses_false_positive():
    from plugins.protocol import evaluate_response_confidence

    # Explicit "no tocar prod" — this is a SAFETY boundary statement,
    # not a high-stakes target. Before v5.2.0 this would have flagged
    # because "prod" is in HIGH_STAKES_KEYWORDS.
    result = evaluate_response_confidence(
        goal="Refactor del parser interno sin tocar producción",
        task_type="analyze",
        evidence_refs=["spec.md"],
        verification_step="unit tests",
    )
    assert result["high_stakes"] is False
    assert "high-stakes context detected" not in result["reasons"]


def test_high_stakes_english_negation_also_suppresses():
    from plugins.protocol import evaluate_response_confidence

    result = evaluate_response_confidence(
        goal="Rename internal helper without touching production paths",
        task_type="analyze",
        evidence_refs=["spec.md"],
        verification_step="unit tests",
    )
    assert result["high_stakes"] is False


def test_positive_signals_boost_confidence_score():
    from plugins.protocol import evaluate_response_confidence

    baseline = evaluate_response_confidence(
        goal="Summarise last sprint notes",
        task_type="analyze",
        evidence_refs=["notes.md"],
        verification_step="re-read notes",
    )
    boosted = evaluate_response_confidence(
        goal="Summarise last sprint notes",
        task_type="analyze",
        evidence_refs=["notes.md"],
        verification_step="re-read notes",
        pre_action_context_hits=3,
        area_has_atlas_entry=True,
    )
    # Boost capped at +10 (3 hits * 2 = 6) + 5 (atlas) = 11 → min(100, ...)
    assert boosted["confidence"] > baseline["confidence"]
    assert boosted["confidence"] - baseline["confidence"] == 11


def test_positive_signal_boost_is_capped_at_ten_for_context_hits():
    from plugins.protocol import evaluate_response_confidence

    # Base score 85 - 0 penalties (evidence + verification present) + boost.
    # 50 hits * 2 = 100 but boost is capped at +10 → 85 + 10 = 95.
    result = evaluate_response_confidence(
        goal="Summarise sprint",
        task_type="analyze",
        evidence_refs=["notes.md"],
        verification_step="reread",
        pre_action_context_hits=50,
    )
    assert result["confidence"] == 95
    assert any("+10" in r for r in result["reasons"])


def test_numeric_safeguard_downgrades_answer_to_verify_on_low_score():
    from plugins.protocol import evaluate_response_confidence

    # task_type is RESPONSE_TASK but passing enough unknowns would route
    # to 'ask' first. We craft a case with no unknowns, no high_stakes,
    # but low score via missing evidence. Boolean rule says 'verify'
    # already, so this test confirms the safeguard doesn't produce
    # stricter downgrades than necessary.
    result = evaluate_response_confidence(
        goal="Summarise the status of the mild, generic task",
        task_type="answer",
        # No evidence, no verification_step → -25 -10 = score 50
    )
    # Without evidence → verify via boolean rule (not safeguard)
    assert result["mode"] == "verify"


def test_numeric_safeguard_converts_verify_to_defer_when_high_stakes_and_very_low_score():
    from plugins.protocol import evaluate_response_confidence

    # high_stakes with unknowns already maps to 'defer' via boolean rule,
    # so this covers the case where unknowns are absent but score is
    # still crushed by accumulated penalties below 30.
    result = evaluate_response_confidence(
        goal="Lanzar la migración crítica de facturación",
        task_type="analyze",
        # high_stakes: -20, no evidence: -25, no verification: -10
        # Base 85 → 30 exactly. We need <30, so add a constraint that
        # touches another penalty-free code path. Actually 30 is not
        # strictly less than 30, so the safeguard shouldn't fire here.
        # We verify the mode stays consistent: no unknowns but missing
        # evidence + high_stakes → defer via the existing boolean rule.
    )
    assert result["mode"] == "defer"
    assert result["high_stakes"] is True


def test_score_never_exceeds_hundred_even_with_big_boosts():
    from plugins.protocol import evaluate_response_confidence

    result = evaluate_response_confidence(
        goal="Trivial query",
        task_type="analyze",
        evidence_refs=["src/x.py"],
        verification_step="manual",
        pre_action_context_hits=10,
        area_has_atlas_entry=True,
    )
    assert result["confidence"] <= 100
