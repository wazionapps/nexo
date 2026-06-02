from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_stack():
    import db
    import failure_prevention

    importlib.reload(db)
    importlib.reload(failure_prevention)
    db.init_db()
    return db, failure_prevention


def _missed_outcome_with_learning(db):
    learning = db.create_learning("nexo-ops", "Avoid duplicate repair rules", "Use the canonical resolver.")
    outcome = db.create_outcome(
        "spec09",
        "Failure prevention test outcome",
        "Should be missed for validation",
        deadline="2026-06-03T00:00:00",
    )
    conn = db.get_db()
    conn.execute(
        "UPDATE outcomes SET status = 'missed', learning_id = ? WHERE id = ?",
        (learning["id"], outcome["id"]),
    )
    conn.commit()
    return learning, outcome


def test_ingest_validated_outcome_dedupes_events_and_redacts(isolated_db):
    db, fp = _reload_stack()
    learning, outcome = _missed_outcome_with_learning(db)

    result = fp.ingest_failure(
        failure_type="workflow",
        area="nexo",
        primary_source_type="outcome_miss",
        primary_source_ref=f"outcome:{outcome['id']}",
        symptom="Assumed /Users/franciscoc/private token=abc123 from 192.168.1.9 provider_payload={raw-secret-after-marker}",
        severity="p1",
        confidence=0.9,
        evidence_refs=[f"outcome:{outcome['id']}"],
    )

    assert result["validated"] is True
    case = result["case"]
    assert case["status"] == "analyzing"
    assert case["frequency_count"] == 1
    assert case["learning_resolution"]["learning_id"] == learning["id"]
    redacted = case["symptom"]["value_redacted"]
    assert "/Users/franciscoc" not in redacted
    assert "abc123" not in redacted
    assert "192.168.1.9" not in redacted
    assert "provider_payload" not in redacted
    assert "raw-secret-after-marker" not in redacted
    assert redacted == "[redacted_payload]"

    duplicate = fp.ingest_failure(
        failure_type="workflow",
        area="nexo",
        primary_source_type="outcome_miss",
        primary_source_ref=f"outcome:{outcome['id']}",
        symptom="Assumed /Users/franciscoc/private token=abc123 from 192.168.1.9 provider_payload={raw-secret-after-marker}",
        severity="p1",
        confidence=0.9,
    )
    assert duplicate["failure_uid"] == result["failure_uid"]
    assert duplicate["source_event_inserted"] is False
    assert duplicate["case"]["frequency_count"] == 1


def test_same_pattern_from_different_source_reinforces_one_case(isolated_db):
    db, fp = _reload_stack()
    _, outcome = _missed_outcome_with_learning(db)
    symptom = "Repeated release failure because verification was skipped"

    first = fp.ingest_failure(
        failure_type="release",
        area="nexo",
        primary_source_type="outcome_miss",
        primary_source_ref=f"outcome:{outcome['id']}",
        symptom=symptom,
        severity="p1",
    )
    second = fp.ingest_failure(
        failure_type="release",
        area="nexo",
        primary_source_type="test_failure",
        primary_source_ref="test:tests/test_failure_prevention.py::test_same_pattern_from_different_source_reinforces_one_case",
        symptom=symptom,
        severity="p1",
    )

    assert second["failure_uid"] == first["failure_uid"]
    assert second["case"]["frequency_count"] == 2
    assert len(second["case"]["source_event_refs"]) == 2


def test_same_source_ref_can_support_distinct_cases(isolated_db):
    _, fp = _reload_stack()
    source_ref = "test:tests/test_failure_prevention.py::test_same_source_ref_can_support_distinct_cases"

    first = fp.ingest_failure(
        failure_type="release",
        area="nexo",
        primary_source_type="test_failure",
        primary_source_ref=source_ref,
        symptom="Release failed because migration path was skipped",
        severity="p1",
    )
    second = fp.ingest_failure(
        failure_type="release",
        area="nexo",
        primary_source_type="test_failure",
        primary_source_ref=source_ref,
        symptom="Release failed because plugin inventory was skipped",
        severity="p1",
    )

    assert first["failure_uid"] != second["failure_uid"]
    assert first["source_event_uid"] != second["source_event_uid"]
    assert first["source_event_inserted"] is True
    assert second["source_event_inserted"] is True
    assert first["case"]["frequency_count"] == 1
    assert second["case"]["frequency_count"] == 1


def test_invalid_ref_stays_candidate_and_does_not_increment_frequency(isolated_db):
    _, fp = _reload_stack()
    result = fp.ingest_failure(
        failure_type="workflow",
        area="nexo",
        primary_source_type="outcome_miss",
        primary_source_ref="outcome:99999",
        symptom="Outcome ref does not exist",
        severity="p1",
    )

    assert result["validated"] is False
    assert result["case"]["status"] == "candidate"
    assert result["case"]["frequency_count"] == 0
    assert result["validation_error"] == "outcome_not_found"


def test_inferred_source_cannot_activate_strong_antibody(isolated_db):
    _, fp = _reload_stack()
    result = fp.ingest_failure(
        failure_type="tool",
        area="nexo",
        primary_source_type="immune_finding",
        primary_source_ref="immune_finding:scan-1:hash-1",
        symptom="Immune scan says maybe broken",
        severity="p1",
    )

    assert result["validated"] is True
    assert result["case"]["status"] == "candidate"

    warn = fp.propose_antibody_action(
        failure_uid=result["failure_uid"],
        action_type="guard_rule_proposal",
        target_system="guardian",
        target_ref="guardian_rule:R99",
        activation_policy="warn",
        verification_ref="test:tests/test_failure_prevention.py::test_inferred_source_cannot_activate_strong_antibody",
        verification_status="pending",
    )
    assert warn["ok"] is False
    assert warn["error"] == "inference_source_must_remain_candidate_only"

    shadow = fp.propose_antibody_action(
        failure_uid=result["failure_uid"],
        action_type="docs_update",
        target_system="docs",
        target_ref="evidence:spec09-doc",
        activation_policy="shadow",
        verification_status="not_applicable",
    )
    assert shadow["ok"] is False
    assert shadow["error"] == "inference_source_must_remain_candidate_only"

    manual = fp.propose_antibody_action(
        failure_uid=result["failure_uid"],
        action_type="docs_update",
        target_system="docs",
        target_ref="evidence:spec09-doc",
        activation_policy="manual_approval_required",
        approved_ref="protocol_task:1",
        verification_status="not_applicable",
    )
    assert manual["ok"] is False
    assert manual["error"] == "inference_source_must_remain_candidate_only"

    candidate = fp.propose_antibody_action(
        failure_uid=result["failure_uid"],
        action_type="docs_update",
        target_system="docs",
        target_ref="evidence:spec09-doc",
        activation_policy="candidate_only",
        verification_status="not_applicable",
    )
    assert candidate["ok"] is True


def test_metadata_and_action_fields_are_redacted_before_persisting(isolated_db):
    _, fp = _reload_stack()
    case = fp.ingest_failure(
        failure_type="workflow",
        area="nexo",
        primary_source_type="test_failure",
        primary_source_ref="test:tests/test_failure_prevention.py::test_metadata_and_action_fields_are_redacted_before_persisting",
        symptom="Metadata should be sanitized",
        severity="p1",
        idempotency_key="token=raw-secret",
        metadata={
            "token": "abc123",
            "safe": "value from /etc/passwd and 10.0.0.1",
            "nested": {"raw_prompt": "provider_payload={secret-after-marker}"},
        },
    )

    case_metadata = case["case"]["metadata"]
    source_metadata = case["source_event"]["metadata"]
    assert "token" not in json.dumps(case_metadata)
    assert "abc123" not in json.dumps(case_metadata)
    assert "/etc/passwd" not in json.dumps(case_metadata)
    assert "10.0.0.1" not in json.dumps(case_metadata)
    assert "provider_payload" not in json.dumps(case_metadata)
    assert "secret-after-marker" not in json.dumps(case_metadata)
    assert source_metadata["idempotency_key_hash"].startswith("sha256:")
    assert "raw-secret" not in json.dumps(source_metadata)

    proposed = fp.propose_antibody_action(
        failure_uid=case["failure_uid"],
        action_type="release_gate_update",
        target_system="release_readiness",
        target_ref="test:tests/test_failure_prevention.py::metadata_gate",
        required_verification="Run from /Users/franciscoc with bearer abcdefghijklmnop",
        metadata={"authorization": "Bearer abcdefghijklmnop", "notes": "raw_response={secret-after-marker} from 192.168.1.8"},
    )

    assert proposed["ok"] is True
    antibody = proposed["antibody"]
    assert "/Users/franciscoc" not in antibody["required_verification"]
    assert "abcdefghijklmnop" not in antibody["required_verification"]
    assert "authorization" not in json.dumps(antibody["metadata"])
    assert "192.168.1.8" not in json.dumps(antibody["metadata"])
    assert "raw_response" not in json.dumps(antibody["metadata"])
    assert "secret-after-marker" not in json.dumps(antibody["metadata"])


def test_sensitive_refs_are_rejected(isolated_db):
    _, fp = _reload_stack()

    ref_with_ip = fp.ingest_failure(
        failure_type="workflow",
        area="nexo",
        primary_source_type="manual_review",
        primary_source_ref="evidence:host-192.168.1.8",
        symptom="Do not persist IP refs",
    )
    assert ref_with_ip["ok"] is False
    assert ref_with_ip["error"] == "ref_contains_ip"

    case = fp.ingest_failure(
        failure_type="workflow",
        area="nexo",
        primary_source_type="test_failure",
        primary_source_ref="test:tests/test_failure_prevention.py::test_sensitive_refs_are_rejected",
        symptom="Safe case",
        severity="p1",
    )
    bad_target = fp.propose_antibody_action(
        failure_uid=case["failure_uid"],
        action_type="docs_update",
        target_system="docs",
        target_ref="evidence:/var/log/nexo.log",
    )
    assert bad_target["ok"] is False
    assert bad_target["error"] == "ref_contains_sensitive_path"


def test_antibody_requires_verification_approval_and_rolls_back(isolated_db):
    _, fp = _reload_stack()
    case = fp.ingest_failure(
        failure_type="release",
        area="nexo",
        primary_source_type="test_failure",
        primary_source_ref="test:tests/test_failure_prevention.py::test_antibody_requires_verification_approval_and_rolls_back",
        symptom="Release gate failed",
        severity="p1",
    )

    blocked = fp.propose_antibody_action(
        failure_uid=case["failure_uid"],
        action_type="release_gate_update",
        target_system="release_readiness",
        target_ref="test:tests/test_failure_prevention.py::gate",
        activation_policy="block_after_verification",
        verification_ref="test:tests/test_failure_prevention.py::gate",
        verification_status="passed",
    )
    assert blocked["ok"] is False
    assert blocked["error"] == "block_requires_passed_verification_and_rollback"

    manual_bad = fp.propose_antibody_action(
        failure_uid=case["failure_uid"],
        action_type="release_gate_update",
        target_system="release_readiness",
        target_ref="test:tests/test_failure_prevention.py::manual",
        activation_policy="manual_approval_required",
        approved_ref="Francisco said ok",
    )
    assert manual_bad["ok"] is False
    assert manual_bad["error"] == "manual_approval_requires_traceable_approved_ref"

    ok = fp.propose_antibody_action(
        failure_uid=case["failure_uid"],
        action_type="release_gate_update",
        target_system="release_readiness",
        target_ref="test:tests/test_failure_prevention.py::gate",
        activation_policy="block_after_verification",
        verification_ref="test:tests/test_failure_prevention.py::gate",
        verification_status="passed",
        rollback_ref="change_log:1",
    )
    assert ok["ok"] is True
    antibody_uid = ok["antibody_uid"]

    fp.mark_false_positive(case["failure_uid"], antibody_uid=antibody_uid, reason="Too noisy")
    false_positive = fp.mark_false_positive(case["failure_uid"], antibody_uid=antibody_uid, reason="Still noisy")
    assert false_positive["case"]["status"] == "conflict_review"
    assert false_positive["case"]["false_positive_count"] == 2

    rolled_back = fp.rollback_antibody_action(antibody_uid, rollback_ref="change_log:2", reason="Rollback verified")
    assert rolled_back["ok"] is True
    assert fp.get_failure_case(case["failure_uid"])["status"] == "rolled_back"


def test_learning_resolver_is_dry_run_owner_for_learning_antibody(isolated_db):
    db, fp = _reload_stack()
    db.create_learning("nexo-ops", "Guard before edit", "Run guard before editing.")
    case = fp.ingest_failure(
        failure_type="workflow",
        area="nexo",
        primary_source_type="test_failure",
        primary_source_ref="test:tests/test_failure_prevention.py::test_learning_resolver_is_dry_run_owner_for_learning_antibody",
        symptom="Guard was skipped",
        severity="p1",
    )

    proposed = fp.propose_antibody_action(
        failure_uid=case["failure_uid"],
        action_type="learning_resolve",
        target_system="learning_resolver",
        target_ref="learning:1",
        metadata={
            "learning_candidate": {
                "category": "nexo-ops",
                "title": "guard before edit",
                "content": "Same rule again.",
                "source_authority": "explicit_instruction",
            }
        },
    )

    assert proposed["ok"] is True
    assert proposed["learning_resolution"]["action"] == "merge"
    assert fp.get_failure_case(case["failure_uid"])["learning_resolution"]["action"] == "merge"


def test_plugin_surfaces_json_without_executing_actions(isolated_db):
    db, _ = _reload_stack()
    _, outcome = _missed_outcome_with_learning(db)
    from plugins import failure_prevention as plugin

    payload = plugin.handle_failure_prevention_ingest(
        failure_type="workflow",
        area="nexo",
        primary_source_type="outcome_miss",
        primary_source_ref=f"outcome:{outcome['id']}",
        symptom="Plugin ingest smoke",
        severity="p1",
    )
    result = json.loads(payload)
    assert result["ok"] is True

    listed = json.loads(plugin.handle_failure_prevention_cases(status="analyzing"))
    assert listed["cases"]
