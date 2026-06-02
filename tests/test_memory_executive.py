from __future__ import annotations

import json
from pathlib import Path


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "memory_executive_cases.json"
REQUIRED_DECISION_KEYS = {
    "decision_kind",
    "destination",
    "owner_module",
    "write_mode",
    "confidence",
    "reason",
    "dedupe_key",
    "ttl_or_decay",
    "privacy_level",
    "redaction_policy",
    "pre_answer_policy",
    "verification_policy",
    "conflict_policy",
    "side_effects",
    "max_latency_ms",
    "requires_shadow_mode",
}


def _fixture_cases() -> list[dict]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return payload["cases"]


def test_memory_executive_contract_returns_typed_actions_for_each_decision():
    import memory_executive

    cases = _fixture_cases()
    assert len(cases) >= 40
    positives = [case for case in cases if case["expected_decision_kind"] != "ignore"]
    negatives = [case for case in cases if case["expected_decision_kind"] == "ignore"]
    assert len(positives) >= 30
    assert len(negatives) >= 8

    covered = set()
    for case in cases:
        decision = memory_executive.decide(
            case["input_event"],
            shadow_mode=bool(case.get("shadow_mode", True)),
        )
        assert set(decision) == REQUIRED_DECISION_KEYS, case["name"]
        assert decision["decision_kind"] == case["expected_decision_kind"], case["name"]
        assert decision["destination"] == case["expected_destination"], case["name"]
        assert decision["owner_module"] == case["expected_owner_module"], case["name"]
        assert decision["write_mode"] == case["expected_write_mode"], case["name"]
        assert decision["dedupe_key"] == case["expected_dedupe_key"], case["name"]
        assert decision["pre_answer_policy"] == case["expected_pre_answer_policy"], case["name"]
        assert decision["privacy_level"] == case["expected_privacy_level"], case["name"]
        assert decision["side_effects"] == case["expected_side_effects"], case["name"]
        assert decision["requires_shadow_mode"] is True, case["name"]
        assert decision["destination"] not in set(case.get("forbidden_destinations") or []), case["name"]
        assert 0.0 <= decision["confidence"] <= 1.0, case["name"]
        assert decision["max_latency_ms"] <= 40, case["name"]
        covered.add(decision["decision_kind"])

    assert {
        "ignore",
        "recent_context",
        "commitment",
        "workflow_checkpoint",
        "decision",
        "learning_candidate",
        "entity_update",
        "risk_signal",
        "preference",
        "evidence_ref",
        "proposed_causal_edge",
        "quarantine",
    } <= covered


def test_memory_executive_repeated_event_is_idempotent_per_destination():
    import memory_executive

    event = next(case["input_event"] for case in _fixture_cases() if case["name"] == "commitment_spanish_future_action")
    first = memory_executive.decide(event)
    second = memory_executive.decide(dict(event))

    assert first["destination"] == "commitments"
    assert first["dedupe_key"] == second["dedupe_key"]
    assert first["decision_kind"] == second["decision_kind"]


def test_memory_executive_deep_sleep_duplicate_reinforces_not_duplicates():
    import memory_executive

    event = next(case["input_event"] for case in _fixture_cases() if case["name"] == "deep_sleep_duplicate_reinforces")
    decision = memory_executive.decide(event)

    assert decision["destination"] == "memory_observation_queue"
    assert decision["write_mode"] == "reinforce"
    assert decision["dedupe_key"] == "memory_observation_queue:ev_original"
    assert decision["conflict_policy"] == "do_not_overwrite"


def test_memory_executive_commitment_action_uses_commitment_ledger_only(isolated_db):
    import db
    import memory_executive

    event = next(case["input_event"] for case in _fixture_cases() if case["name"] == "commitment_english_future_action")
    before = db.list_commitments(session_id="sid", limit=10)
    decision = memory_executive.decide(event, shadow_mode=True)
    after = db.list_commitments(session_id="sid", limit=10)

    assert before == []
    assert after == []
    assert decision["destination"] == "commitments"
    assert "followup" not in json.dumps(decision)
    assert "reminder" not in json.dumps(decision)


def test_memory_executive_workflow_checkpoint_uses_existing_workflow_idempotency():
    import memory_executive

    event = next(case["input_event"] for case in _fixture_cases() if case["name"] == "workflow_update_event")
    decision = memory_executive.decide(event)

    assert decision["destination"] == "workflow_runs"
    assert decision["write_mode"] == "update"
    assert decision["dedupe_key"] == "workflow_runs:sid:idem_workflow_update"


def test_memory_executive_entity_update_updates_existing_entity_or_flags_conflict():
    import memory_executive

    update = next(case["input_event"] for case in _fixture_cases() if case["name"] == "entity_project_update")
    conflict = next(case["input_event"] for case in _fixture_cases() if case["name"] == "entity_conflict_review")

    update_decision = memory_executive.decide(update)
    conflict_decision = memory_executive.decide(conflict)

    assert update_decision["write_mode"] == "update"
    assert conflict_decision["write_mode"] == "review"
    assert conflict_decision["verification_policy"] == "conflict_review"
    assert conflict_decision["conflict_policy"] == "do_not_overwrite"


def test_memory_executive_preference_contradiction_goes_to_review_not_replace():
    import memory_executive

    event = next(case["input_event"] for case in _fixture_cases() if case["name"] == "preference_conflict_review")
    decision = memory_executive.decide(event)

    assert decision["destination"] == "preferences"
    assert decision["write_mode"] == "review"
    assert decision["verification_policy"] == "conflict_review"
    assert decision["conflict_policy"] == "do_not_overwrite"


def test_memory_executive_pre_answer_policy_does_not_cold_load_llm(monkeypatch):
    import builtins
    import memory_executive

    imported = []
    real_import = builtins.__import__

    def tracking_import(name, *args, **kwargs):
        imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)
    event = next(case["input_event"] for case in _fixture_cases() if case["name"] == "recent_tool_context")
    decision = memory_executive.decide(event)

    assert decision["pre_answer_policy"] == "cached_hint_only"
    assert "semantic_router" not in imported
    assert "call_model_raw" not in imported


def test_memory_executive_general_noise_ignored_multilingual():
    import memory_executive

    noise_cases = [
        case for case in _fixture_cases()
        if case["name"].startswith("general_")
    ]
    assert noise_cases
    for case in noise_cases:
        decision = memory_executive.decide(case["input_event"])
        assert decision["decision_kind"] == "ignore", case["name"]
        assert decision["destination"] == "none", case["name"]
        assert decision["pre_answer_policy"] == "never", case["name"]


def test_memory_executive_redacts_secret_payloads_before_audit_storage():
    import memory_executive

    event = next(case["input_event"] for case in _fixture_cases() if case["name"] == "secret_token_quarantine")
    decision = memory_executive.decide(event)
    audit = memory_executive.audit_record(event, decision)
    blob = json.dumps(audit, ensure_ascii=False)

    assert decision["destination"] == "cognitive_quarantine"
    assert decision["redaction_policy"] == "drop"
    assert "sk_live_1234567890abcdef" not in blob
    assert "token=" not in blob


def test_memory_executive_shadow_mode_has_no_side_effects(isolated_db):
    import db
    import memory_executive

    event = next(case["input_event"] for case in _fixture_cases() if case["name"] == "preference_explicit")
    before = db.list_preferences()
    decision = memory_executive.decide(event, shadow_mode=True)
    after = db.list_preferences()

    assert decision["destination"] == "preferences"
    assert before == after


def test_memory_executive_sensitive_memory_asks_permission_without_revealing_payload():
    import memory_executive

    event = next(case["input_event"] for case in _fixture_cases() if case["name"] == "entity_person_sensitive")
    decision = memory_executive.decide(event)
    audit = memory_executive.audit_record(event, decision)

    assert decision["privacy_level"] == "sensitive"
    assert decision["redaction_policy"] == "reference_only"
    assert decision["verification_policy"] == "manual_review"
    assert decision["pre_answer_policy"] == "allowed_if_explicit"
    assert "Client A" not in json.dumps(audit)


def test_record_memory_event_embeds_shadow_decision_without_creating_commitment(isolated_db):
    import db

    event = db.record_memory_event(
        event_type="future_action",
        source_type="assistant_text",
        source_id="record-shadow-commitment",
        session_id="sid-record-shadow",
        project_key="nexo",
        metadata={"statement": "I will review the release checklist"},
        idempotency_key="record-shadow-commitment",
        created_at=1000.0,
    )

    assert event["ok"] is True
    executive = event["metadata"]["memory_executive"]
    assert executive["decision_kind"] == "commitment"
    assert executive["destination"] == "commitments"
    assert db.list_commitments(session_id="sid-record-shadow", limit=10) == []


def test_record_memory_event_secret_payload_does_not_enter_observation_queue(isolated_db):
    import db

    event = db.record_memory_event(
        event_type="tool_output",
        source_type="tool_call",
        source_id="record-shadow-secret",
        session_id="sid-record-secret",
        project_key="nexo",
        metadata={"summary": "token=sk_live_1234567890abcdef"},
        idempotency_key="record-shadow-secret",
        created_at=1000.0,
    )
    conn = db.get_db()
    queued = conn.execute(
        "SELECT COUNT(*) FROM memory_observation_queue WHERE event_uid = ?",
        (event["event_uid"],),
    ).fetchone()[0]

    assert event["ok"] is True
    assert event["metadata"]["memory_executive"]["decision_kind"] == "quarantine"
    assert event["metadata"]["memory_executive"]["privacy_level"] == "secret"
    assert queued == 0
