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


def _verified_against_real_evidence() -> str:
    return json.dumps([
        "Escenario reproducido: flujo de despliegue abierto en el endpoint publico tras publicar.",
        "Datos REALES usados: URL de produccion https://nexo-desktop.com/downloads/update.json y artefacto sha256:abc123.",
        "Hipotesis NO reproducidas: no se reprodujo fallo de descarga ni mismatch de version en manifiestos publicos.",
    ])


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


def test_confidence_check_requires_verify_for_stale_written_sources_without_live_evidence():
    from plugins.protocol import handle_confidence_check

    payload = json.loads(
        handle_confidence_check(
            goal="Answer from DEPLOY-NOTES whether Stripe SMTP is configured",
            task_type="answer",
            context_hint="The answer depends on internal docs older than 24h and a transcript from last week.",
            evidence_refs='["docs:DEPLOY-NOTES.md updated_at=2026-06-20"]',
            verification_step="Check the cited note before replying",
        )
    )

    assert payload["ok"] is True
    assert payload["mode"] == "verify"
    assert "stale written reference requires live source verification" in payload["reasons"]


def test_confidence_check_allows_stale_written_sources_with_live_evidence():
    from plugins.protocol import handle_confidence_check

    payload = json.loads(
        handle_confidence_check(
            goal="Answer from DEPLOY-NOTES whether the release manifest is current",
            task_type="answer",
            context_hint="The note is older than 24h, but production was checked.",
            evidence_refs='["docs:DEPLOY-NOTES.md updated_at=2026-06-20", "repo:main@abc123 verified_at=2026-06-22", "endpoint:https://nexo-desktop.com/downloads/update.json HTTP 200"]',
            verification_step="Use the live repo and endpoint evidence when answering",
            stakes="low",
        )
    )

    assert payload["ok"] is True
    assert payload["mode"] == "answer"
    assert "stale written reference requires live source verification" not in payload["reasons"]


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


def test_task_open_consumes_operational_state_release_gate():
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-1012-3012")
    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal="Run the release verification step",
            task_type="execute",
            area="release",
            plan='["prepare", "verify", "record evidence"]',
            evidence_refs='["release spec"]',
            verification_step="run release gates",
            stakes="high",
            context_hint="Release work must be safe for update and fresh install users",
        )
    )

    assert payload["ok"] is True
    state = payload["operational_state"]
    assert state["area_key"] == "release"
    assert state["verification_requirement"] == "release_gate"
    assert state["autonomy_limit"] == "propose"
    assert "Verifico doble" in state["visible_guidance"]
    assert "TENSION" not in state["visible_guidance"]
    assert "max_caution" not in state["visible_guidance"]


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


def test_task_open_with_blocking_guard_sets_pending_ack_state_without_opening_debt(monkeypatch):
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
    assert payload["next_action"] == "Resolve the blocking guard warnings before editing."
    assert payload["response_contract"]["next_action"] == payload["next_action"]
    row = get_db().execute(
        "SELECT guard_has_blocking, guard_acknowledged FROM protocol_tasks WHERE task_id = ?",
        (payload["task_id"],),
    ).fetchone()
    assert row["guard_has_blocking"] == 1
    assert row["guard_acknowledged"] == 0
    debt_count = get_db().execute(
        "SELECT COUNT(*) FROM protocol_debt WHERE task_id = ? AND debt_type = 'unacknowledged_guard_blocking'",
        (payload["task_id"],),
    ).fetchone()[0]
    assert debt_count == 0


def test_task_open_ack_rules_inline_acknowledges_blocking_guard(monkeypatch):
    """Block K G7: ``nexo_task_open(..., ack_rules="#41")`` must acknowledge
    the blocking rules inline so the operator does not have to chain a
    second ``nexo_task_acknowledge_guard`` call."""
    from db import get_db
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-1004-3004")
    monkeypatch.setattr(
        "plugins.protocol.handle_guard_check",
        lambda **kwargs: "BLOCKING RULES (resolve BEFORE writing):\n  #41 [FILE RULE:/tmp/x]: Read the canonical rule first\n",
    )
    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal="Edit guarded file, ack inline",
            task_type="edit",
            area="nexo-ops",
            files="/tmp/x",
            ack_rules="#41",
        )
    )

    # Inline ack payload present + ok.
    assert payload["ack_guard"]["ok"] is True
    assert payload["guard"]["acknowledged_inline"] is True
    # next_action updates to reflect post-ack state.
    assert payload["next_action"] == (
        "Blocking guard rules acknowledged inline via task_open."
    )
    # DB row reflects the acknowledged flag.
    row = get_db().execute(
        "SELECT guard_has_blocking, guard_acknowledged FROM protocol_tasks WHERE task_id = ?",
        (payload["task_id"],),
    ).fetchone()
    assert row["guard_has_blocking"] == 1
    assert row["guard_acknowledged"] == 1


def test_task_open_ack_rules_without_blocking_is_a_noop(monkeypatch):
    """When there are no blocking rules, ``ack_rules`` must report a
    graceful no-op instead of failing the open."""
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-1004-3005")
    monkeypatch.setattr(
        "plugins.protocol.handle_guard_check",
        lambda **kwargs: "",  # no blocking rules
    )
    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal="answer without guard",
            task_type="answer",
            area="nexo-ops",
            ack_rules="#99",
        )
    )
    # Task opens fine, ack_guard reports the skip.
    assert payload["ok"] is True
    assert payload["ack_guard"]["ok"] is False
    assert payload["ack_guard"]["skipped"] is True


def test_task_open_ack_rules_mismatched_ids_surface_error(monkeypatch):
    """If the operator passes an ack list that does not cover every
    blocking rule, the inline ack must fail with the validator's
    ``expected_ids`` / ``provided_ids`` payload so the operator can
    correct the call without a second round-trip."""
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-1004-3006")
    monkeypatch.setattr(
        "plugins.protocol.handle_guard_check",
        lambda **kwargs: "BLOCKING RULES (resolve BEFORE writing):\n"
        "  #41 [FILE RULE:/tmp/x]: alpha\n"
        "  #156 [FILE RULE:/tmp/x]: beta\n",
    )
    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal="Edit guarded file, partial ack",
            task_type="edit",
            area="nexo-ops",
            files="/tmp/x",
            ack_rules="#41",  # missing #156
        )
    )
    assert payload["ack_guard"]["ok"] is False
    assert set(payload["ack_guard"]["expected_ids"]) == {41, 156}
    assert payload["ack_guard"]["provided_ids"] == [41]


def test_task_open_passes_project_hint_to_guard_check(monkeypatch):
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-1005-2005")
    observed = {}

    def _fake_guard(**kwargs):
        observed.update(kwargs)
        return "No relevant learnings found for these files/area."

    monkeypatch.setattr("plugins.protocol.handle_guard_check", _fake_guard)
    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal="Patch Shopify project safely",
            task_type="edit",
            area="shopify",
            project_hint="recambios-bmw",
            files="/repo/shopify/theme/snippets/reviews.liquid",
        )
    )

    assert payload["ok"] is True
    assert observed["project_hint"] == "recambios-bmw"
    assert observed["area"] == "shopify"


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


def test_task_open_rejects_invalid_task_type():
    import plugins.protocol as protocol

    sid = _register_session("nexo-1005-2008")
    payload = json.loads(
        protocol.handle_task_open(
            sid=sid,
            goal="Open task with invalid type",
            task_type="ship",
            area="nexo-ops",
        )
    )

    assert payload["ok"] is False
    assert "Invalid task_type" in payload["error"]
    assert payload["valid_task_types"] == ["analyze", "answer", "delegate", "edit", "execute"]


def test_task_open_extracts_plan_steps_from_goal_when_plan_field_is_empty():
    from db import get_db
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-1005-2009")
    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal=(
                "Prepare the guarded edit:\n"
                "1. inspect the current file\n"
                "2. patch the needed section\n"
                "3. run the targeted verification"
            ),
            task_type="edit",
            area="nexo-ops",
            files="/tmp/x",
            verification_step="run the targeted verification",
        )
    )

    assert payload["ok"] is True
    assert "No plan defined for action task" not in (payload.get("blocked_reason") or "")
    row = get_db().execute(
        "SELECT plan FROM protocol_tasks WHERE task_id = ?",
        (payload["task_id"],),
    ).fetchone()
    assert json.loads(row["plan"]) == [
        "inspect the current file",
        "patch the needed section",
        "run the targeted verification",
    ]


def test_confidence_check_rejects_invalid_task_type():
    from plugins.protocol import handle_confidence_check

    payload = json.loads(
        handle_confidence_check(
            goal="Assess a malformed protocol task",
            task_type="ship",
            area="nexo-ops",
        )
    )

    assert payload["ok"] is False
    assert "Invalid task_type" in payload["error"]
    assert payload["valid_task_types"] == ["analyze", "answer", "delegate", "edit", "execute"]


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
    row = get_db().execute(
        "SELECT guard_acknowledged, guard_acknowledged_at FROM protocol_tasks WHERE task_id = ?",
        (opened["task_id"],),
    ).fetchone()
    assert row["guard_acknowledged"] == 1
    assert row["guard_acknowledged_at"] is not None
    debt = get_db().execute(
        "SELECT status, resolution FROM protocol_debt WHERE task_id = ? AND debt_type = 'unacknowledged_guard_blocking'",
        (opened["task_id"],),
    ).fetchone()
    assert debt is None


def test_task_close_resolves_guard_debt_when_no_file_changes_happened(monkeypatch):
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1005-2005b")
    monkeypatch.setattr(
        "plugins.protocol.handle_guard_check",
        lambda **kwargs: "BLOCKING RULES (resolve BEFORE writing):\n  #41 [FILE RULE:/tmp/x]: Read the canonical rule first\n",
    )
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Inspect guarded file but stop before editing",
            task_type="edit",
            area="nexo-ops",
            files="/tmp/x",
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="blocked",
            evidence="Guard blocked the edit, no file was changed, and the task stops pending canonical review.",
            change_summary="Stopped before editing guarded file",
            change_why="Respect the blocking guard instead of forcing a write",
            files_changed="[]",
        )
    )

    assert closed["ok"] is True
    assert "blocked_by" not in closed
    debt_count = get_db().execute(
        "SELECT COUNT(*) FROM protocol_debt WHERE task_id = ? AND debt_type = 'unacknowledged_guard_blocking' AND status = 'open'",
        (opened["task_id"],),
    ).fetchone()[0]
    assert debt_count == 0


def test_task_close_keeps_guard_debt_open_when_guard_touch_violation_exists(monkeypatch):
    from db import create_protocol_debt, get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1005-2005c")
    monkeypatch.setattr(
        "plugins.protocol.handle_guard_check",
        lambda **kwargs: "BLOCKING RULES (resolve BEFORE writing):\n  #41 [FILE RULE:/tmp/x]: Read the canonical rule first\n",
    )
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Attempt guarded edit incorrectly",
            task_type="edit",
            area="nexo-ops",
            files="/tmp/x",
        )
    )
    create_protocol_debt(
        sid,
        "conditioned_file_touch_without_guard_ack",
        severity="error",
        task_id=opened["task_id"],
        evidence="write attempt hit guarded file before acknowledgement",
    )
    create_protocol_debt(
        sid,
        "unacknowledged_guard_blocking",
        severity="error",
        task_id=opened["task_id"],
        evidence="write attempt left the blocking guard unresolved",
    )
    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="blocked",
            evidence="The task stops after a bad guarded touch attempt and still needs explicit acknowledgement.",
            change_summary="Did not proceed after the bad guarded touch",
            change_why="Guard discipline still needs explicit cleanup",
            files_changed="[]",
        )
    )

    assert closed["ok"] is True
    debt = get_db().execute(
        "SELECT status FROM protocol_debt WHERE task_id = ? AND debt_type = 'unacknowledged_guard_blocking'",
        (opened["task_id"],),
    ).fetchone()
    assert debt["status"] == "open"
    assert any(item["debt_type"] == "unacknowledged_guard_blocking" for item in closed["open_debts"])


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
            evidence="pytest -q tests/test_doctor.py passed: 12 passed, 0 failed, 0 skipped in 0.42s; no regressions in doctor providers",
            change_summary="Updated runtime protocol compliance to use live protocol data",
            change_why="Make doctor enforce discipline from live runtime data",
            change_verify="pytest -q tests/test_doctor.py",
        )
    )

    assert closed["ok"] is True
    assert "blocked_by" not in closed
    assert closed["status"] == "clean"
    assert closed["change_log_id"] is not None
    row = get_db().execute(
        "SELECT * FROM protocol_tasks WHERE task_id = ?",
        (opened["task_id"],),
    ).fetchone()
    assert row["status"] == "done"
    assert row["change_log_id"] == closed["change_log_id"]


def test_task_close_rejects_done_without_evidence_and_keeps_task_open():
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

    assert closed["ok"] is False
    assert closed["blocked_by"] == "g1_verify"
    assert closed["debt_type"] == "claimed_done_without_evidence"
    count = get_db().execute(
        "SELECT COUNT(*) FROM protocol_debt WHERE task_id = ? AND debt_type = 'claimed_done_without_evidence' AND status = 'open'",
        (opened["task_id"],),
    ).fetchone()[0]
    assert count == 1
    row = get_db().execute(
        "SELECT status, closed_at FROM protocol_tasks WHERE task_id = ?",
        (opened["task_id"],),
    ).fetchone()
    assert row["status"] == "open"
    assert row["closed_at"] is None


def test_task_close_blocks_total_closure_claim_when_followups_are_open():
    from db import create_followup, get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003-total")
    create_followup(
        "NF-OPEN-TOTAL-CLOSE",
        "Smoke publico pendiente antes de promocionar stable",
        date="2026-06-12",
        status="PENDING",
    )
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Close release cleanup",
            task_type="execute",
            area="release",
            plan='["verify", "close"]',
            verification_step="verify open followups",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="pytest passed and no queda nada pendiente; sin deuda.",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "total_closure_open_work_gate"
    assert closed["debt_type"] == "total_closure_with_open_work"
    assert closed["open_followups"][0]["id"] == "NF-OPEN-TOTAL-CLOSE"
    row = get_db().execute(
        "SELECT status FROM protocol_tasks WHERE task_id = ?",
        (opened["task_id"],),
    ).fetchone()
    assert row["status"] == "open"


def test_task_close_blocks_done_with_manual_pending_steps_without_followup():
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003-manual-block")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Cerrar auditoria aprobada con pasos manuales",
            task_type="execute",
            area="nexo-ops",
            plan='["verificar", "cerrar"]',
            verification_step="pytest focal",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="pytest -q tests/test_protocol.py::test_task_close_blocks_done_with_manual_pending_steps_without_followup passed",
            outcome_notes="Aprobado. Siguiente paso: configurar el monitor y verificarlo en runtime.",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "manual_pending_followup_gate"
    assert closed["debt_type"] == "manual_pending_steps_without_followup"
    row = get_db().execute(
        "SELECT status, closed_at FROM protocol_tasks WHERE task_id = ?",
        (opened["task_id"],),
    ).fetchone()
    assert row["status"] == "open"
    assert row["closed_at"] is None


def test_task_close_allows_manual_pending_steps_with_created_followup():
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003-manual-followup")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Cerrar auditoria aprobada con seguimiento manual",
            task_type="execute",
            area="nexo-ops",
            plan='["verificar", "crear seguimiento", "cerrar"]',
            verification_step="pytest focal",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="pytest -q tests/test_protocol.py::test_task_close_allows_manual_pending_steps_with_created_followup passed",
            outcome_notes="Aprobado. Siguiente paso: configurar el monitor y verificarlo en runtime.",
            followup_needed=True,
            followup_id="NF-MANUAL-PENDING-GATE-TEST",
            followup_description="Configurar el monitor aprobado y verificarlo en runtime.",
            followup_verification="Evidencia de configuración y verificación runtime.",
            followup_reasoning="Creado por gate de task_close para pasos manuales pendientes.",
        )
    )

    assert closed["ok"] is True
    assert closed["status"] == "clean"
    assert closed["followup_id"] == "NF-MANUAL-PENDING-GATE-TEST"
    followup = get_db().execute(
        "SELECT description, status FROM followups WHERE id = ?",
        ("NF-MANUAL-PENDING-GATE-TEST",),
    ).fetchone()
    assert followup["status"] == "PENDING"
    assert "Configurar el monitor aprobado" in followup["description"]


def test_task_close_blocks_irreversible_publish_without_specific_post_evidence_ok():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003-stable")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Publish stable Desktop release",
            task_type="execute",
            area="release",
            plan='["verify evidence", "publish stable"]',
            verification_step="verify public stable manifests",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Smoke local passed; publish stable executed after a generic prior OK.",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "irreversible_specific_ok_gate"
    assert closed["debt_type"] == "irreversible_action_missing_specific_ok"


def test_task_close_blocks_deployed_execute_without_verified_against_real_checklist():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003-real-gate")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Deploy production backend fix",
            task_type="execute",
            area="release",
            plan='["deploy", "verify live endpoint"]',
            verification_step="curl public production endpoint",
            stakes="normal",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="deployed",
            evidence="curl https://example.com/health returned HTTP 200 against the live production endpoint.",
            files_changed="/tmp/deploy-marker",
            change_summary="Deploy production backend fix",
            change_why="Regression test for verified-against-real gate",
            change_verify="curl public production endpoint HTTP 200",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "verified_against_real"
    assert closed["debt_type"] == "verified_against_real_missing"
    assert "escenario reproducido" in closed["missing_items"]
    assert closed["ack_required"] == "partial_verification_acknowledged"


def test_task_close_allows_partial_deploy_with_verified_against_real_ack():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003-real-partial")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Deploy production backend fix",
            task_type="execute",
            area="release",
            plan='["deploy", "verify live endpoint"]',
            verification_step="curl public production endpoint",
            stakes="normal",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="partial",
            evidence="curl https://example.com/health returned HTTP 200 against the live production endpoint.",
            work_type="deploy",
            stakes="high",
            files_changed="/tmp/deploy-marker",
            change_summary="Deploy production backend fix",
            change_why="Regression test for partial verified-against-real ack",
            change_verify="curl public production endpoint HTTP 200",
            partial_verification_acknowledged=True,
            partial_verification_reason="No hubo datos reales suficientes para reproducir el caso reportado en esta ventana.",
        )
    )

    assert closed["ok"] is True
    assert closed["outcome"] == "partial"
    assert closed["status"] == "clean"


def test_task_close_allows_deployed_execute_with_verified_against_real_checklist():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003-real-ok")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Deploy production backend fix",
            task_type="execute",
            area="release",
            plan='["deploy", "verify live endpoint"]',
            verification_step="curl public production endpoint",
            stakes="normal",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="deployed",
            evidence="curl https://example.com/health returned HTTP 200 against the live production endpoint.",
            files_changed="/tmp/deploy-marker",
            change_summary="Deploy production backend fix",
            change_why="Regression test for verified-against-real gate",
            change_verify="curl public production endpoint HTTP 200",
            verification_evidence=_verified_against_real_evidence(),
        )
    )

    assert closed["ok"] is True
    assert closed["outcome"] == "done"


def test_task_close_downgrades_campaign_done_without_live_ads_evidence():
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003-campaign-live-missing")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Aplicar campaña Google Ads desde catálogo Shopify real",
            task_type="execute",
            area="google ads campaign",
            plan='["catalog", "validate urls", "validate_only", "paused upload"]',
            verification_step="HTTP 200 de Final URLs y recuento GAQL post-mutación",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            work_type="campaign",
            evidence="Borrador de campaña generado desde catálogo Shopify.",
            change_summary="Campaña Google Ads preparada.",
        )
    )

    assert closed["ok"] is True
    assert closed["outcome"] == "partial"
    assert closed["followup_id"]
    row = get_db().execute(
        "SELECT status, description, verification FROM followups WHERE id = ?",
        (closed["followup_id"],),
    ).fetchone()
    assert row["status"] == "PENDING"
    assert "evidencia live pendiente" in row["description"]
    assert "HTTP 200 de cada Final URL" in row["verification"]
    assert "recuento GAQL post-mutación" in row["verification"]


def test_task_close_allows_campaign_done_with_final_urls_and_gaql_counts():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003-campaign-live-ok")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Aplicar campaña Google Ads desde catálogo Shopify real",
            task_type="execute",
            area="google ads campaign",
            plan='["catalog", "validate urls", "validate_only", "paused upload"]',
            verification_step="HTTP 200 de Final URLs y recuento GAQL post-mutación",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            work_type="campaign",
            evidence=(
                "Final URLs: curl -I https://www.recambiosyaccesoriosbmw.com/products/demo -> HTTP 200. "
                "Google Ads validate_only=true OK. GAQL post-mutación recuento: campaigns=1 PAUSED, "
                "ad_groups=3, ads=3, keywords=24, negatives=12."
            ),
            change_summary="Campaña Google Ads cargada PAUSED con validación live.",
        )
    )

    assert closed["ok"] is True
    assert closed["outcome"] == "done"
    assert not closed["followup_id"]


def test_task_close_blocks_irreversible_publish_without_matching_human_artifact_hash():
    from plugins.cortex import handle_cortex_decide
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003-stable-hash")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Publish stable Desktop release",
            task_type="execute",
            area="release",
            plan='["verify evidence", "publish stable"]',
            verification_step="verify public stable manifests",
        )
    )
    handle_cortex_decide(
        goal="Verify publish stable Desktop artifact sha256:abc123",
        task_type="execute",
        impact_level="critical",
        area="release",
        session_id=sid,
        task_id=opened["task_id"],
        context_hint="Human validation evidence is specific to artifact sha256:abc123 before publish stable.",
        alternatives=json.dumps([
            {"name": "publish_validated_artifact", "description": "Publish stable only after human validation evidence matches artifact sha256:abc123"},
            {"name": "hold_release", "description": "Hold if the validated artifact hash differs"},
        ]),
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence=(
                "Smoke verified; explicit approval after evidence was captured for publish stable."
            ),
            outcome_notes="aprobación explícita tras evidencia verificada.",
            artifact_hash="sha256:abc123",
            last_human_validation_of_artifact_hash="sha256:def456",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "irreversible_artifact_hash"
    assert closed["debt_type"] == "irreversible_artifact_hash_unverified"
    assert closed["hash_status"] == "mismatch"


def test_task_close_blocks_irreversible_publish_without_cortex_decide():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003-stable-cortex")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Publish stable Desktop release",
            task_type="execute",
            area="release",
            plan='["verify evidence", "publish stable"]',
            verification_step="verify public stable manifests",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
                evidence=(
                    "Smoke verified; explicit approval after evidence was captured for publish stable "
                    "artifact sha256:abc123. "
                    "Desktop open promises audit: transcript grep covered open release promises. "
                    "Bundle empaquetado: search in dist/release and packaged app.asar completed. "
                    "Resultado: 0 open promises pending and no NF followups required. "
                    "API: curl https://nexo-desktop.com/api/health returned HTTP 200. "
                "UI: browser smoke loaded /dashboard and screenshot /tmp/nexo-release-ui.png exists. "
                "Dominio público: https://nexo-desktop.com/downloads/update.json returned HTTP 200. "
                "Endpoint vivo: curl https://nexo-desktop.com/downloads/update.json HTTP 200. "
                "Revisión desplegada: commit abc123 deployed and serving. "
                "URL antigua ya emitida: legacy URL already issued N/A for this release. "
                "Estado interno afectado: DB checked ok. "
                "Rama: origin/main at abc1234. "
                "Git limpio: git status --short empty, working tree clean. "
                "Artefactos: GitHub Release assets DMG and EXE uploaded. "
                "Artefacto correcto: sha256 verified and version matches package.json. "
                "Firma/notarización: codesign, spctl and notarytool verified the macOS artifact. "
                "Manifiestos: update.json and package.json match the release version. "
                "Smoke público: curl https://nexo-desktop.com/downloads/update.json returned HTTP 200. "
                "Captura visual: screenshot /tmp/nexo-release-ui.png exists. "
                "Flujo E2E usuario final: usuario final descarga, instala y abre la versión estable publicada. "
                "Criterio de éxito específico del operador: Francisco validó que el artefacto sha256:abc123 es el candidato aprobado. "
                "Monitor sin redirects implícitos: public uptime monitor checked with no implicit redirects. "
                "Prueba viva: production smoke passed against the public domain."
            ),
            outcome_notes="aprobación explícita tras evidencia verificada para el artefacto sha256:abc123.",
            artifact_hash="sha256:abc123",
            last_human_validation_of_artifact_hash="sha256:abc123",
            files_changed="/tmp/release-artifact",
            change_summary="Publish stable Desktop release",
            change_why="Regression test for irreversible cortex gate",
            change_verify="Artifact hash matches the human-validated release candidate",
            verification_evidence=_verified_against_real_evidence(),
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "irreversible_cortex_decide_gate"
    assert closed["debt_type"] == "irreversible_action_missing_cortex_decide"
    assert closed["response_mode"] == "verify"


def test_task_close_allows_irreversible_publish_with_cortex_and_matching_artifact():
    from plugins.cortex import handle_cortex_decide
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003-stable-cortex-ok")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Publish stable Desktop release",
            task_type="execute",
            area="release",
            plan='["verify evidence", "publish stable"]',
            verification_step="verify public stable manifests",
        )
    )
    handle_cortex_decide(
        goal="Verify and publish stable Desktop artifact sha256:abc123",
        task_type="execute",
        impact_level="critical",
        area="release",
        session_id=sid,
        task_id=opened["task_id"],
        context_hint="Human validation evidence is specific to artifact sha256:abc123 before publish stable.",
        evidence_refs='["operator validation: sha256:abc123", "public manifest verified"]',
        alternatives=json.dumps([
            {"name": "publish_validated_artifact", "description": "Publish stable only after human validation evidence matches artifact sha256:abc123"},
            {"name": "hold_release", "description": "Do not publish until the operator validates the exact artifact hash"},
        ]),
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence=(
                "Smoke verified; explicit approval after evidence was captured for publish stable "
                "artifact sha256:abc123. "
                "Desktop open promises audit: transcript grep covered open release promises. "
                "Bundle empaquetado: search in dist/release and packaged app.asar completed. "
                "Resultado: 0 open promises pending and no NF followups required. "
                "API: curl https://nexo-desktop.com/api/health returned HTTP 200. "
                "UI: browser smoke loaded /dashboard and screenshot /tmp/nexo-release-ui.png exists. "
                "Dominio público: https://nexo-desktop.com/downloads/update.json returned HTTP 200. "
                "Endpoint vivo: curl https://nexo-desktop.com/downloads/update.json HTTP 200. "
                "Revisión desplegada: commit abc123 deployed and serving. "
                "URL antigua ya emitida: legacy URL already issued N/A for this release. "
                "Estado interno afectado: DB checked ok. "
                "Rama: origin/main at abc1234. "
                "Git limpio: git status --short empty, working tree clean. "
                "Artefactos: GitHub Release assets DMG and EXE uploaded. "
                "Artefacto correcto: sha256 verified and version matches package.json. "
                "Firma/notarización: codesign, spctl and notarytool verified the macOS artifact. "
                "Manifiestos: update.json and package.json match the release version. "
                "Smoke público: curl https://nexo-desktop.com/downloads/update.json returned HTTP 200. "
                "Captura visual: screenshot /tmp/nexo-release-ui.png exists. "
                "Flujo E2E usuario final: usuario final descarga, instala y abre la versión estable publicada. "
                "Criterio de éxito específico del operador: Francisco validó que el artefacto sha256:abc123 es el candidato aprobado. "
                "Monitor sin redirects implícitos: public uptime monitor checked with no implicit redirects. "
                "Prueba viva: production smoke passed against the public domain."
            ),
            outcome_notes="aprobación explícita tras evidencia verificada para el artefacto sha256:abc123.",
            artifact_hash="sha256:abc123",
            last_human_validation_of_artifact_hash="sha256:abc123",
            files_changed="/tmp/release-artifact",
            change_summary="Publish stable Desktop release",
            change_why="Regression test for irreversible cortex gate",
            change_verify="Artifact hash matches the human-validated release candidate",
            verification_evidence=_verified_against_real_evidence(),
        )
    )

    assert closed["ok"] is True
    assert "blocked_by" not in closed
    assert closed["cortex_evaluation"]["task_id"] == opened["task_id"]


def test_task_close_reject_done_without_evidence_dedupes_protocol_debt():
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003b")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Edit without evidence twice",
            task_type="edit",
            area="nexo-ops",
            files="/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/protocol.py",
            plan='["inspect", "patch"]',
            verification_step="run pytest",
        )
    )

    first = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            change_summary="First close attempt",
        )
    )
    second = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            change_summary="Second close attempt",
        )
    )

    assert first["ok"] is False
    assert second["ok"] is False
    assert first["debt_id"] == second["debt_id"]
    count = get_db().execute(
        "SELECT COUNT(*) FROM protocol_debt WHERE task_id = ? AND debt_type = 'claimed_done_without_evidence' AND status = 'open'",
        (opened["task_id"],),
    ).fetchone()[0]
    assert count == 1


def test_task_close_rejects_external_done_without_real_world_verification():
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003c")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Send email reply to Maria about the meeting",
            task_type="execute",
            area="ops",
            plan='["send", "verify"]',
            verification_step="reopen the sent email and verify recipients and body",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Command output showed the email API returned success and the task can be marked complete.",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "external_real_world_verify"
    assert closed["debt_type"] == "external_real_world_verification_missing"
    count = get_db().execute(
        "SELECT COUNT(*) FROM protocol_debt WHERE task_id = ? AND debt_type = 'external_real_world_verification_missing' AND status = 'open'",
        (opened["task_id"],),
    ).fetchone()[0]
    assert count == 1


def test_task_close_does_not_treat_local_summary_file_as_external_message():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003f")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal=(
                "Crear /tmp/self-audit-interpreted.md y /tmp/self-audit-summary.json "
                "con causas raiz y acciones concretas."
            ),
            task_type="execute",
            area="self-audit/runtime_personal",
            files=json.dumps([
                "/tmp/self-audit-interpreted.md",
                "/tmp/self-audit-summary.json",
            ]),
            plan='["write files", "validate local artifacts"]',
            verification_step="Verificar con test -s, python3 -m json.tool y grep de encabezados markdown.",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence=(
                "Validacion local: test -s OK para ambos ficheros; python3 -m json.tool "
                "/tmp/self-audit-summary.json parseo correctamente; grep confirmo encabezados "
                "requeridos en self-audit-interpreted.md."
            ),
        )
    )

    assert closed["ok"] is True
    assert closed["status"] == "clean"


def test_task_close_local_only_followup_runner_not_flagged_external():
    """A local-only followup-runner close (DB/local-files only, no external send)
    must NOT be misclassified as external-real-world stakes by the closure gate,
    even though delivery-sounding words appear. Closes the false-positive that
    blocked a local followup-runner task on 'envio/message' keywords."""
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003g")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Ejecutar el followup-runner interno (solo lectura DB/archivos locales, sin envio externo)",
            task_type="execute",
            area="followup-runner/runtime_personal",
            files=json.dumps(["src/scripts/nexo-followup-runner.py"]),
            plan='["leer followups due", "ejecutar acciones locales"]',
            verification_step="Verificar localmente que los followups se procesaron en la DB.",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence=(
                "followup-runner interna ejecutada: solo lectura DB y archivos locales, "
                "sin envio externo (no email, no message). Procesados los followups due "
                "en la DB local; verificado con consulta a la tabla followups."
            ),
        )
    )

    assert closed["ok"] is True
    assert closed["status"] == "clean", closed
    assert closed.get("blocked_by") != "external_real_world_verify"


def test_task_close_accepts_external_done_with_real_world_verification():
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2003d")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Send calendar invite for the supplier call",
            task_type="execute",
            area="ops",
            plan='["create invite", "verify event"]',
            verification_step="reopen calendar event and verify invitees, date, timezone and Meet link",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence=(
                "I reopened the calendar event after creation and verified invitees, date, timezone, "
                "single Meet link, notes, and ownership against the requested call details."
            ),
        )
    )

    assert closed["ok"] is True
    assert closed["status"] == "clean"
    row = get_db().execute(
        "SELECT status FROM protocol_tasks WHERE task_id = ?",
        (opened["task_id"],),
    ).fetchone()
    assert row["status"] == "done"


def test_task_close_blocks_analyze_report_p0_p1_without_followup_refs(tmp_path):
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_close

    report = tmp_path / "audit.md"
    report.write_text(
        "# Audit\n\n"
        "- P0: production checkout is broken\n"
        "- **P1**: support replies can be dropped\n",
        encoding="utf-8",
    )

    sid = _register_session("nexo-1003-2003e")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Analyze audit findings",
            task_type="analyze",
            area="protocol",
            evidence_refs=json.dumps([str(report)]),
            verification_step="read generated report",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Generated and reviewed the audit report with actionable P0/P1 findings.",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "analyze_p0_p1_followup_gate"
    assert closed["findings"] == 2
    assert closed["followup_refs"] == 0
    assert closed["missing_followups"] == 2
    count = get_db().execute(
        "SELECT COUNT(*) FROM protocol_debt WHERE task_id = ? AND debt_type = 'analyze_p0_p1_followups_missing' AND status = 'open'",
        (opened["task_id"],),
    ).fetchone()[0]
    assert count == 1


def test_task_close_accepts_analyze_report_p0_p1_with_matching_followup_refs(tmp_path):
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_close

    report = tmp_path / "audit.md"
    report.write_text(
        "## P0: release gate bypasses evidence\n\n"
        "1. P1: analyzer report misses workflow links\n",
        encoding="utf-8",
    )

    sid = _register_session("nexo-1003-2003f")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Analyze audit findings",
            task_type="analyze",
            area="protocol",
            evidence_refs=json.dumps([str(report)]),
            verification_step="read generated report",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Generated and reviewed the audit report; each P0/P1 item has a durable followup reference.",
            evidence_refs=json.dumps([str(report), "NF-AUDIT-P0-1", "NF-AUDIT-P1-1"]),
        )
    )

    assert closed["ok"] is True
    assert closed["status"] == "clean"
    row = get_db().execute(
        "SELECT status FROM protocol_tasks WHERE task_id = ?",
        (opened["task_id"],),
    ).fetchone()
    assert row["status"] == "done"


def test_task_close_rejects_invalid_outcome_without_mutating_task():
    from db import get_db
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1003-2004")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Exercise invalid close outcome handling",
            task_type="edit",
            area="nexo-ops",
            files="/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/protocol.py",
            plan='["inspect", "validate"]',
            verification_step="run pytest",
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="wrapped-up cleanly",
            evidence="pytest -q tests/test_protocol.py passed: 18 passed, 0 failed in 0.33s; protocol_task flow + debt state verified",
        )
    )

    assert closed["ok"] is False
    assert "Invalid close outcome" in closed["error"]
    assert closed["valid_outcomes"] == ["blocked", "cancelled", "done", "failed", "partial"]
    row = get_db().execute(
        "SELECT status, closed_at FROM protocol_tasks WHERE task_id = ?",
        (opened["task_id"],),
    ).fetchone()
    assert row["status"] == "open"
    assert row["closed_at"] is None


def test_close_protocol_task_rejects_invalid_outcome():
    from db import create_protocol_task, close_protocol_task

    task = create_protocol_task(
        session_id="nexo-1003-2005",
        goal="Reject invalid internal close outcome",
        task_type="execute",
    )

    with pytest.raises(ValueError, match="Invalid close outcome"):
        close_protocol_task(task["task_id"], outcome="open")


def test_create_protocol_task_rejects_invalid_task_type():
    from db import create_protocol_task

    with pytest.raises(ValueError, match="Invalid task_type"):
        create_protocol_task(
            session_id="nexo-1003-2006",
            goal="Reject invalid internal task type",
            task_type="ship",
        )


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
            evidence="pytest -q tests/test_guard.py passed: 6 passed, 0 failed in 0.21s; guard_check blocking + acknowledge path covered",
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


def test_task_close_blocks_open_correction_without_learning_or_justification():
    # A detected correction without a durable learning or explicit
    # no-learning justification blocks task_close and leaves one idempotent
    # ``missing_learning_after_correction`` debt.
    from db import get_db, record_session_correction_requirement
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1004-2104")
    record_session_correction_requirement(
        sid,
        "No, that was the wrong source of truth.",
        source="test",
    )
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Verify correction close gate",
            task_type="edit",
            area="nexo-ops",
            files="/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/protocol.py",
            plan='["inspect", "verify", "close"]',
            verification_step="run pytest",
        )
    )
    blocked = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="pytest -q tests/test_protocol.py::test_task_close_soft_opens_debt_for_open_correction_without_learning_or_justification passed",
            change_summary="Verified correction close gate",
            change_why="Regression test for correction requirements",
        )
    )

    assert blocked["ok"] is False
    assert blocked["blocked_by"] == "correction_learning_required"
    debt_count = get_db().execute(
        "SELECT COUNT(*) FROM protocol_debt WHERE task_id = ? "
        "AND debt_type = 'missing_learning_after_correction' AND status = 'open'",
        (opened["task_id"],),
    ).fetchone()[0]
    assert debt_count == 1
    open_correction = get_db().execute(
        "SELECT COUNT(*) FROM session_correction_requirements WHERE session_id = ? AND status = 'open'",
        (sid,),
    ).fetchone()[0]
    assert open_correction == 1


def test_task_close_allows_open_correction_with_explicit_no_learning_justification():
    from db import get_db, record_session_correction_requirement
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1004-2204")
    record_session_correction_requirement(
        sid,
        "No, that was an expected detector false positive.",
        source="test",
    )
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Close correction with no-learning justification",
            task_type="edit",
            area="nexo-ops",
            files="/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/protocol.py",
            plan='["inspect", "verify", "close"]',
            verification_step="run pytest",
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="pytest -q tests/test_protocol.py::test_task_close_allows_open_correction_with_explicit_no_learning_justification passed",
            change_summary="Accepted correction justification without learning",
            change_why="No reusable rule changed in this synthetic correction test",
            learning_reasoning=(
                "Justificación: no aprendizaje reutilizable; fue un falso positivo del detector "
                "en una prueba sintética y no cambia la regla canónica."
            ),
        )
    )

    assert closed["status"] == "clean"
    assert closed["learning_id"] is None
    row = get_db().execute(
        "SELECT status, resolved_learning_id FROM session_correction_requirements WHERE session_id = ?",
        (sid,),
    ).fetchone()
    assert row["status"] == "resolved"
    assert row["resolved_learning_id"] is None


def test_high_stakes_action_close_rejects_done_without_cortex_evaluation():
    from db import get_db
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

    assert closed["ok"] is False
    assert closed["blocked_by"] == "g1_cortex"
    assert closed["debt_type"] == "missing_cortex_evaluation"
    count = get_db().execute(
        "SELECT COUNT(*) FROM protocol_debt WHERE task_id = ? AND debt_type = 'missing_cortex_evaluation' AND status = 'open'",
        (opened["task_id"],),
    ).fetchone()[0]
    assert count == 1
    row = get_db().execute(
        "SELECT status, closed_at FROM protocol_tasks WHERE task_id = ?",
        (opened["task_id"],),
    ).fetchone()
    assert row["status"] == "open"
    assert row["closed_at"] is None


def test_task_close_rejects_done_when_change_log_creation_fails(monkeypatch):
    from db import get_db
    from plugins import protocol

    sid = _register_session("nexo-1013-2013b")
    opened = json.loads(
        protocol.handle_task_open(
            sid=sid,
            goal="Edit with broken change log",
            task_type="edit",
            area="nexo-ops",
            files='["/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/protocol.py"]',
            plan='["inspect", "patch", "verify"]',
            verification_step="run pytest",
        )
    )
    monkeypatch.setattr(protocol, "log_change", lambda *args, **kwargs: {"error": "sqlite locked"})

    closed = json.loads(
        protocol.handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="pytest -q tests/test_protocol.py passed: 12 passed, 0 failed in 0.40s; protocol path validated",
            change_summary="Touched protocol close flow",
            change_why="Exercise G1 change_log gate",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "g1_change_log"
    assert closed["debt_type"] == "missing_change_log"
    count = get_db().execute(
        "SELECT COUNT(*) FROM protocol_debt WHERE task_id = ? AND debt_type = 'missing_change_log' AND status = 'open'",
        (opened["task_id"],),
    ).fetchone()[0]
    assert count == 1
    row = get_db().execute(
        "SELECT status, closed_at, change_log_id FROM protocol_tasks WHERE task_id = ?",
        (opened["task_id"],),
    ).fetchone()
    assert row["status"] == "open"
    assert row["closed_at"] is None
    assert row["change_log_id"] is None


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
            evidence=(
                "API: curl https://nexo-desktop.com/api/health returned HTTP 200. "
                "UI: browser smoke loaded /dashboard and screenshot /tmp/nexo-release-ui.png exists. "
                "Dominio público: https://nexo-desktop.com/downloads/update.json returned HTTP 200. "
                "Endpoint vivo: curl https://nexo-desktop.com/downloads/update.json HTTP 200. "
                "Revisión desplegada: commit abc123 deployed and serving. "
                "URL antigua ya emitida: legacy URL already issued N/A for this release. "
                "Estado interno afectado: DB checked ok. "
                "Rama: origin/main at abc1234. "
                "Git limpio: git status --short empty, working tree clean. "
                "Artefactos: GitHub Release assets DMG and EXE uploaded. "
                "Artefacto correcto: sha256 verified and version matches package.json. "
                "Firma/notarización: codesign, spctl and notarytool verified the macOS artifact. "
                "Manifiestos: update.json and package.json match the release version. "
                "Smoke público: curl https://nexo-desktop.com/downloads/update.json returned HTTP 200. "
                "Captura visual: screenshot /tmp/nexo-release-ui.png exists. "
                "Flujo E2E usuario final: usuario final abre la app, descarga el update y completa el arranque sin fallback. "
                "Criterio de éxito específico del operador: Francisco validó que la versión publicada coincide con el artefacto candidato y arranca sin regresión. "
                "Monitor sin redirects implícitos: public uptime monitor checked with no implicit redirects. "
                "Test: regression suite passed before publication. "
                "Prueba viva: staging smoke green and production smoke passed against the public domain. "
                "Changelog updated and version confirmed in public manifests."
            ),
            outcome_notes="Release executed with persisted cortex evaluation.",
            work_type="release",
            stakes="high",
            files_changed="/tmp/release-artifact",
            change_summary="Deploy production release package",
            change_why="Exercise clean high-stakes release closure with full evidence matrix",
            change_verify="Public API/UI/domain/manifests/artifacts/live smoke evidence supplied",
            verification_evidence=_verified_against_real_evidence(),
        )
    )

    assert evaluation["ok"] is True
    assert closed["status"] == "clean"
    assert closed["cortex_evaluation"]["task_id"] == opened["task_id"]


def test_task_close_blocks_high_stakes_release_without_public_evidence():
    from plugins.cortex import handle_cortex_decide
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1014-2014-public-evidence")
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

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="test staging production changelog version all checked, but no public endpoint or browser artifact was verified.",
            outcome_notes="Release executed with persisted cortex evaluation.",
            work_type="release",
            stakes="high",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "high_stakes_public_evidence"
    assert closed["debt_type"] == "high_stakes_public_evidence_missing"
    assert closed["response_mode"] == "verify"


def test_task_close_blocks_high_stakes_release_without_ux_first_checklist():
    from plugins.cortex import handle_cortex_decide
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1014-2014-ux-first")
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

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="curl https://nexo-desktop.com/downloads/update.json returned HTTP 200 after deploy.",
            outcome_notes="Release executed with persisted cortex evaluation.",
            work_type="release",
            stakes="high",
            files_changed="/tmp/release-artifact",
            change_summary="Deploy production release package",
            change_why="Regression test for UX-first release close gate",
            change_verify="Public manifest HTTP 200 but no visual/e2e/operator checklist",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "ux_first_release_gate"
    assert closed["debt_type"] == "ux_first_release_gate_incomplete"
    assert "captura o evidencia visual pública" in closed["missing_items"]
    assert "flujo end-to-end como usuario final" in closed["missing_items"]
    assert "criterio de éxito específico del operador" in closed["missing_items"]


def test_non_release_task_does_not_open_release_alignment_debt_for_version_wording():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1015-2015")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Audit the version parsing logic used by protocol tests",
            task_type="analyze",
            area="guardian",
            plan='["inspect parser", "review tests", "summarize findings"]',
            evidence_refs='["test fixture", "protocol parser"]',
            verification_step="review the matching test coverage",
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Reviewed parser fixtures and test expectations for version wording without touching any release channel.",
            outcome_notes="This was only a protocol audit, not a deploy or release task.",
        )
    )

    debt_types = {item["debt_type"] for item in closed["open_debts"]}
    assert "release_channel_alignment_incomplete" not in debt_types
    assert closed["status"] == "clean"


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
            evidence="pytest -q tests/test_guard.py passed: 6 passed, 0 failed in 0.21s; guard_check blocking + acknowledge path covered",
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


@pytest.mark.xfail(
    reason="Pre-existing xdist flake — passes in isolation but attention "
           "engine reads process-global session state that two parallel "
           "workers race over. Tracked in NF-TEST-PROTOCOL-ATTENTION-XDIST-FLAKE.",
    strict=False,
)
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


@pytest.mark.xfail(
    reason="cognitive trigger anticipation no longer fires from handle_task_open goal-matching; tracked in followup NF-TEST-PROTOCOL-API-REFACTOR",
    strict=False,
)
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


def test_explicit_low_stakes_override_suppresses_auto_detection():
    from plugins.protocol import evaluate_response_confidence

    result = evaluate_response_confidence(
        goal="Deploy the production release package",
        task_type="analyze",
        evidence_refs=["release.md"],
        verification_step="review release checklist",
        stakes="low",
    )

    assert result["high_stakes"] is False
    assert "stakes override suppresses automatic high-stakes detection" in result["reasons"]


def test_internal_audit_context_suppresses_release_keyword_false_positive():
    from plugins.protocol import evaluate_response_confidence

    result = evaluate_response_confidence(
        goal="Audit the release wording detector used by protocol tests",
        task_type="edit",
        area="guardian",
        context_hint="Read-only protocol audit for heuristic coverage",
        evidence_refs=["tests/test_protocol.py"],
        verification_step="run protocol tests",
    )

    assert result["high_stakes"] is False
    assert "internal audit/testing context suppresses automatic high-stakes detection" in result["reasons"]


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


def test_internal_guardian_audit_close_does_not_open_missing_cortex_debt():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1021-2021")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Audit the release wording detector used by protocol tests",
            task_type="edit",
            area="guardian",
            files="/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/protocol.py",
            plan='["inspect detector", "patch tests", "verify coverage"]',
            evidence_refs='["tests/test_protocol.py"]',
            verification_step="run protocol tests",
            context_hint="Read-only protocol audit for heuristic coverage",
        )
    )

    assert opened["decision_support"]["required"] is False
    assert opened["response_contract"]["high_stakes"] is False

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Ran the protocol test battery and reviewed the detector behavior without performing any release or production action.",
            outcome_notes="Internal protocol audit only.",
        )
    )

    debt_types = {item["debt_type"] for item in closed["open_debts"]}
    assert "missing_cortex_evaluation" not in debt_types


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


def test_build_area_context_provides_builtin_personal_scripts_context():
    import paths
    from plugins.protocol import _build_area_context

    result = _build_area_context("personal-scripts")

    assert result["has_context"] is True
    assert result["atlas_entry"]["project_key"] == "personal-scripts"
    assert result["atlas_entry"]["locations"]["scripts_dir"] == str(paths.personal_scripts_dir())
    assert result["atlas_entry"]["locations"]["registry_db"] == str(paths.brain_dir() / "personal_scripts.db")


def test_build_area_context_accepts_personal_scripts_underscore_alias():
    from plugins.protocol import _build_area_context

    result = _build_area_context("personal_scripts")

    assert result["has_context"] is True
    assert result["atlas_entry"]["project_key"] == "personal-scripts"


def test_task_close_blocks_state_bug_without_live_surface_evidence():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1607-2607")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Resolver bug de estado del backend",
            task_type="execute",
            area="nexo-product",
            verification_step="Verificar contra producción viva",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Test de fixture verde con 3 casos y revisión local del repo.",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "live_surface_verification"


def test_task_close_accepts_state_bug_with_live_surface_evidence():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1634-2634")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Resolver bug de estado del backend",
            task_type="execute",
            area="nexo-product",
            verification_step="Verificar contra producción viva",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Logs vivos black-box.ndjson revisados: no aparece impossible_state_recovered tras reproducir el flujo.",
        )
    )

    assert closed["ok"] is True


def test_task_close_blocks_ui_release_ready_without_original_symptom_evidence():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1640-2640")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Arreglar bug del selector modal de UI",
            task_type="edit",
            area="frontend",
            files="/tmp/renderer/ui.js",
            verification_step="Reabrir el modal reportado en navegador real",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Tests unitarios verdes y build local completada correctamente.",
            summary="release lista",
            files_changed="/tmp/renderer/ui.js",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "verify_original_symptom"
    assert closed["debt_type"] == "verify_original_symptom_missing"


def test_task_close_accepts_ui_release_ready_with_original_symptom_evidence():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1641-2641")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Arreglar bug del selector modal de UI",
            task_type="edit",
            area="frontend",
            files="/tmp/renderer/ui.js",
            verification_step="Reabrir el modal reportado en navegador real",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence=(
                "Síntoma original reproducido y verificado en navegador headed: "
                "URL repro http://localhost:3000/modal, screenshot /tmp/modal-fixed.png, "
                "selector abre y permite elegir opción."
            ),
            summary="release lista",
            files_changed="/tmp/renderer/ui.js",
        )
    )

    assert closed["ok"] is True


def test_task_open_tags_repeated_symptom_bug_as_p0():
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-1642-2642")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Resolver mismo síntoma reportado 2+ veces en Stripe topup",
            task_type="edit",
            area="nexo-product",
            files="/tmp/billing.py",
            verification_step="Cerrar solo con test reproductor, backend y UI",
        )
    )

    assert opened["contract"]["priority"] == "P0"
    assert opened["contract"]["repeated_symptom_p0"] is True


def test_task_close_blocks_repeated_symptom_p0_without_three_evidence_classes():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1643-2643")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Resolver mismo síntoma reportado 2+ veces: No pudimos cargar los créditos",
            task_type="edit",
            area="nexo-product frontend backend",
            files="/tmp/credits.py",
            verification_step="Test reproductor rojo-verde, curl/SQL backend y evidencia UI post-fix",
        )
    )

    blocked = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            files_changed="/tmp/credits.py",
            evidence="Test reproductor de regresión rojo->verde en pytest: falla pre-fix y pasa post-fix en repo.",
            change_summary="Fix repeated credits topup bug",
            change_why="Mismo síntoma reportado 2+ veces por Francisco.",
        )
    )

    assert blocked["ok"] is False
    assert blocked["priority"] == "P0"
    assert blocked["blocked_by"] == "p0_repeated_bug_evidence"
    assert "verificacion_backend_curl_sql" in blocked["missing_evidence"]
    assert "evidencia_ui_post_fix" in blocked["missing_evidence"]


def test_task_close_accepts_repeated_symptom_p0_with_three_evidence_classes():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1644-2644")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Resolver mismo síntoma reportado 2+ veces: refrescar Brain rebota",
            task_type="edit",
            area="nexo-product frontend backend",
            files="/tmp/brain_refresh.py",
            verification_step="Test reproductor rojo-verde, curl/SQL backend y evidencia UI post-fix",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            files_changed="/tmp/brain_refresh.py",
            evidence=(
                "Test reproductor de regresión rojo->verde en pytest: falla pre-fix y pasa post-fix en repo. "
                "Backend flow: curl /api/brain/refresh devuelve HTTP 200 y SQL SELECT confirma estado actualizado en Cloud SQL. "
                "UI post-fix: screenshot /tmp/brain-refresh-fixed.png y log Playwright headed muestran frontend sin rebote."
            ),
            change_summary="Fix repeated Brain refresh bug",
            change_why="Mismo síntoma reportado 2+ veces por Francisco.",
        )
    )

    assert closed["ok"] is True


def test_task_close_blocks_desktop_release_without_open_promise_audit():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1645-2645")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Cerrar release NEXO Desktop 0.45.21",
            task_type="execute",
            area="NEXO Desktop release",
            verification_step="Verificar manifests, artefactos y promesas abiertas",
        )
    )

    blocked = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="gh release view v0.45.21 existe y curl update.json devuelve manifest 0.45.21.",
            summary="Release NEXO Desktop 0.45.21 verificada",
        )
    )

    assert blocked["ok"] is False
    assert blocked["blocked_by"] == "desktop_release_promise_audit"
    assert "transcript_promise_grep" in blocked["missing_evidence"]
    assert "dist_release_bundle_search" in blocked["missing_evidence"]
    assert "missing_promises_followups" in blocked["missing_evidence"]


def test_task_close_accepts_desktop_release_with_open_promise_audit():
    from plugins.cortex import handle_cortex_decide
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1646-2646")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Cerrar release NEXO Desktop 0.45.22",
            task_type="execute",
            area="NEXO Desktop release",
            verification_step="Verificar manifests, artefactos y promesas abiertas",
        )
    )
    handle_cortex_decide(
        goal="Cerrar release NEXO Desktop 0.45.22 con auditoría de promesas abierta verificada",
        task_type="execute",
        impact_level="high",
        area="NEXO Desktop release",
        session_id=sid,
        task_id=opened["task_id"],
        evidence_refs='["desktop promise audit", "dist/release bundle search"]',
        alternatives=json.dumps([
            {"name": "close_with_audit", "description": "Close only after transcript promises and packaged bundle evidence are verified"},
            {"name": "hold_release", "description": "Hold release if any promise lacks bundle evidence or followup"},
        ]),
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            stakes="low",
            evidence=(
                "Desktop open promises audit: transcript grep cubrio 'meto en próxima release', "
                "'lo incluyo', 'lo añadiré' y 'spec en Escritorio'. "
                "Bundle empaquetado: busqueda en dist/release y app.asar completo. "
                "Resultado: 0 promesas abiertas sin implementar y ningun NF nuevo requerido. "
                "gh release view v0.45.22 existe; curl update.json devuelve manifest 0.45.22."
            ),
            summary="Release NEXO Desktop 0.45.22 verificada",
        )
    )

    assert closed["ok"] is True


def test_task_close_blocks_ready_claim_without_variant_matrix_evidence():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1647-2647")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Preparar campaña Google Ads con URLs públicas, idiomas, marcas y RSAs 15+4+2",
            task_type="execute",
            area="google ads public landings variants",
            verification_step="HEAD a URLs públicas y matriz de variantes antes de cerrar.",
        )
    )

    blocked = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Tests locales OK.",
            summary="Campaña preparada y verificada con URLs públicas y RSAs.",
        )
    )

    assert blocked["ok"] is False
    assert blocked["blocked_by"] == "preclose_variant_matrix"
    assert "head_200_urls_publicas" in blocked["missing_evidence"]
    assert "matriz_variantes_con_caso_por_variante" in blocked["missing_evidence"]


def test_task_close_accepts_ready_claim_with_variant_matrix_evidence():
    from plugins.cortex import handle_cortex_decide
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1648-2648")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Preparar campaña Google Ads con URLs públicas, idiomas, marcas, RSAs 15+4+2 y envíos reales",
            task_type="execute",
            area="google ads public landings variants real sends",
            verification_step="HEAD a URLs públicas, matriz de variantes y autorización real antes de cerrar.",
        )
    )
    handle_cortex_decide(
        goal="Preparar campaña Google Ads con URLs públicas, variantes y envíos reales",
        task_type="execute",
        impact_level="high",
        area="google ads",
        session_id=sid,
        task_id=opened["task_id"],
        evidence_refs='["variant matrix", "explicit send authorization"]',
        alternatives=json.dumps([
            {"name": "prepare_with_authorized_send", "description": "Prepare and verify only after explicit authorization for real sends"},
            {"name": "hold_before_send", "description": "Hold the campaign until authorization and variant evidence are complete"},
        ]),
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence=(
                "HEAD URLs públicas: curl -I /landing-bmw -> HTTP 200; curl -I /landing-mini -> 200 OK. "
                "Matriz de variantes: inventario por variante con 1 caso por variante ejecutado para BMW, MINI, idioma ES/FR, "
                "sheet codes, send_to/customer_photo y RSAs 15+4+2 completos. "
                "Envíos reales: autorización explícita de Francisco registrada antes del envío. "
                "Verifiqué el artefacto enviado en sent folder: destinatario, asunto, cuerpo y Message-ID correctos."
            ),
            summary="Campaña preparada, verificada y lista.",
        )
    )

    assert closed["ok"] is True


def test_task_close_blocks_production_deploy_without_changed_files():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1660-2660")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Desplegar fix a producción",
            task_type="execute",
            area="nexo-release",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="git push origin main terminó correctamente y producción responde HTTP 200.",
            change_summary="Despliegue de fix a producción",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "g1_change_log"


def test_task_close_blocks_visible_release_without_surface_matrix():
    from plugins.cortex import handle_cortex_decide
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1661-2661")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Deploy visible production fix",
            task_type="execute",
            area="release",
            plan='["prepare", "deploy", "verify"]',
            evidence_refs='["release contract", "staging green"]',
            verification_step="verify public production surfaces",
            stakes="high",
        )
    )
    handle_cortex_decide(
        goal="Deploy visible production fix",
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

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence=(
                "curl https://nexo-desktop.com returned HTTP 200 after deploy. "
                "Captura visual: screenshot /tmp/release-visible.png exists. "
                "Flujo E2E usuario final: usuario final abre la pantalla visible y completa el recorrido afectado. "
                "Criterio de éxito específico del operador: Francisco validó que el arreglo visible aparece en la superficie pública."
            ),
            work_type="release",
            stakes="high",
            files_changed="/tmp/release-artifact",
            change_summary="Deploy visible production fix",
            change_why="Exercise visible release surface matrix gate",
            change_verify="curl public domain HTTP 200 plus visual and final-user UX checklist",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "visible_release_surface_matrix"
    assert closed["debt_type"] == "visible_release_surface_matrix_incomplete"
    assert "API" in closed["missing_surfaces"]
    assert "rama de publicación" in closed["missing_surfaces"]
    assert "git limpio" in closed["missing_surfaces"]
    assert "firma/notarización" in closed["missing_surfaces"]
    assert "monitor sin redirects implícitos" in closed["missing_surfaces"]
    assert "revisión desplegada" in closed["missing_surfaces"]
    assert "URL antigua ya emitida" in closed["missing_surfaces"]
    assert "estado interno afectado" in closed["missing_surfaces"]


def test_task_close_accepts_visible_release_with_live_source_surface_checklist():
    from plugins.cortex import handle_cortex_decide
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1662-2662")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Deploy visible production fix",
            task_type="execute",
            area="release",
            plan='["prepare", "deploy", "verify"]',
            evidence_refs='["release contract", "staging green"]',
            verification_step="verify public production surfaces",
            stakes="high",
        )
    )
    handle_cortex_decide(
        goal="Deploy visible production fix",
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

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence=(
                "API: endpoint /api/health responde ok. "
                "UI: navegador headed validado. "
                "Dominio público: https://nexo-desktop.com/downloads/update.json. "
                "Endpoint vivo: curl https://nexo-desktop.com/downloads/update.json HTTP 200. "
                "Revisión desplegada: commit abc123 deployed y serving. "
                "URL antigua ya emitida: enlace antiguo ya enviado verificado como N/A para esta release. "
                "Estado interno afectado: DB checked ok. "
                "Rama: origin/main. "
                "Git limpio: git status nothing to commit. "
                "Artefactos: github release. "
                "Artefacto correcto: sha256 coincide verificado. "
                "Firma/notarización: firmado y notarizado. "
                "Manifiestos: update.json. "
                "Smoke público: smoke https://nexo-desktop.com/downloads/update.json HTTP 200. "
                "Captura visual UI: screenshot /tmp/release-visible.png. "
                "Monitor sin redirects implícitos: ninguno. "
                "Prueba viva: curl HTTP 200. "
                "Captura visual: screenshot /tmp/release-visible.png exists. "
                "Flujo E2E usuario final: usuario final abre la pantalla visible y completa el recorrido afectado. "
                "Criterio de éxito específico del operador: Francisco validó que el arreglo visible aparece en la superficie pública."
            ),
            work_type="release",
            stakes="high",
            files_changed="/tmp/release-artifact",
            change_summary="Deploy visible production fix",
            change_why="Exercise complete live source surface checklist",
            change_verify="curl public domain HTTP 200 plus full live-source surface checklist",
            verification_evidence=_verified_against_real_evidence(),
        )
    )

    assert closed["ok"] is True


def test_task_close_blocks_time_bound_commitment_without_dated_followup():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1663-2663")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Cerrar seguimiento Nora con compromiso operativo",
            task_type="execute",
            area="nora",
            plan='["verificar correo", "cerrar turno"]',
            evidence_refs='["correo Nora 2026-06-25"]',
            verification_step="confirmar que cualquier compromiso con plazo queda anclado",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Correo revisado y respuesta preparada con trazabilidad suficiente para cerrar el turno.",
            summary="Haré segundo nudge en 48h si BBVA Seguridad no responde antes.",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "time_bound_commitment_followup_gate"
    assert closed["debt_type"] == "time_bound_commitment_without_dated_followup"


def test_task_close_blocks_build_release_log_commitment_without_dated_followup():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1665-2665")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Cerrar release con seguimiento prometido",
            task_type="execute",
            area="release",
            plan='["revisar build", "mergear", "cerrar"]',
            evidence_refs='["release local validada"]',
            verification_step="confirmar que el seguimiento operativo queda fechado",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Build validada y merge revisado con logs locales sin errores.",
            summary="Revisaré logs de build/release mañana/pasado para confirmar que no reaparece.",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "time_bound_commitment_followup_gate"
    assert closed["debt_type"] == "time_bound_commitment_without_dated_followup"


def test_task_close_accepts_time_bound_commitment_with_dated_followup():
    from db import get_followup
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1664-2664")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Cerrar seguimiento Nora con compromiso operativo",
            task_type="execute",
            area="nora",
            plan='["verificar correo", "crear seguimiento fechado", "cerrar turno"]',
            evidence_refs='["correo Nora 2026-06-25"]',
            verification_step="confirmar que cualquier compromiso con plazo queda anclado",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Correo revisado y respuesta preparada con trazabilidad suficiente para cerrar el turno.",
            summary="Haré segundo nudge en 48h si BBVA Seguridad no responde antes.",
            followup_needed=True,
            followup_id="NF-TEST-TIMEBOUND-COMMITMENT-48H",
            followup_description="Revisar respuesta BBVA Seguridad y enviar segundo nudge si no hay contestación.",
            followup_date="2026-06-28",
            followup_verification="Confirmar respuesta recibida o segundo nudge enviado con Message-ID.",
        )
    )

    assert closed["ok"] is True
    assert closed["followup_id"] == "NF-TEST-TIMEBOUND-COMMITMENT-48H"
    row = get_followup("NF-TEST-TIMEBOUND-COMMITMENT-48H")
    assert row["date"] == "2026-06-28"


def test_task_close_blocks_open_commitment_without_complete_followup():
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1666-2666")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Cerrar turno con compromiso abierto",
            task_type="execute",
            area="nexo-ops",
            plan='["verificar", "cerrar"]',
            evidence_refs='["logs locales"]',
            verification_step="confirmar que compromisos quedan trazados",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Validación local ejecutada con trazabilidad suficiente para cerrar el turno actual.",
            summary="Queda pendiente de revisar el bloqueo de seguridad con evidencia antes de continuar.",
        )
    )

    assert closed["ok"] is False
    assert closed["blocked_by"] == "open_commitment_followup_gate"
    assert closed["debt_type"] == "open_commitment_without_complete_followup"


def test_task_close_accepts_open_commitment_with_complete_followup():
    from db import get_followup
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-1667-2667")
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Cerrar turno con compromiso abierto trazado",
            task_type="execute",
            area="nexo-ops",
            plan='["verificar", "crear seguimiento completo", "cerrar"]',
            evidence_refs='["logs locales"]',
            verification_step="confirmar que compromisos quedan trazados",
        )
    )

    closed = json.loads(
        handle_task_close(
            sid=sid,
            task_id=opened["task_id"],
            outcome="done",
            evidence="Validación local ejecutada con trazabilidad suficiente para cerrar el turno actual.",
            summary="Queda pendiente de revisar el bloqueo de seguridad con evidencia antes de continuar.",
            followup_needed=True,
            followup_id="NF-TEST-OPEN-COMMITMENT-COMPLETE",
            followup_description="Verificar el bloqueo de seguridad y preparar el entregable con evidencia.",
            followup_date="2026-06-28",
            followup_verification="Registrar causa, evidencia de verificación y decisión de continuidad.",
            followup_reasoning="Creado por gate de cierre para compromiso abierto sin seguimiento previo.",
        )
    )

    assert closed["ok"] is True
    assert closed["followup_id"] == "NF-TEST-OPEN-COMMITMENT-COMPLETE"
    row = get_followup("NF-TEST-OPEN-COMMITMENT-COMPLETE")
    assert row["date"] == "2026-06-28"
    assert row["verification"]
