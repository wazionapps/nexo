"""Failure prevention plugin tools."""

from __future__ import annotations

import json

from failure_prevention import (
    get_failure_case,
    ingest_failure,
    list_failure_cases,
    mark_false_positive,
    propose_antibody_action,
    rollback_antibody_action,
    validate_source_ref,
)


def _json_arg(value: str, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def handle_failure_prevention_ingest(
    failure_type: str,
    area: str,
    primary_source_type: str,
    primary_source_ref: str,
    symptom: str,
    trigger: str = "",
    missed_signal: str = "",
    wrong_assumption: str = "",
    root_cause: str = "",
    corrective_action: str = "",
    severity: str = "p3",
    confidence: float = 0.5,
    entity_refs: str = "[]",
    evidence_refs: str = "[]",
    privacy_level: str = "normal",
    idempotency_key: str = "",
    metadata: str = "{}",
) -> str:
    """Create or reinforce a redacted non-authoritative failure case."""
    result = ingest_failure(
        failure_type=failure_type,
        area=area,
        primary_source_type=primary_source_type,
        primary_source_ref=primary_source_ref,
        symptom=symptom,
        trigger=trigger,
        missed_signal=missed_signal,
        wrong_assumption=wrong_assumption,
        root_cause=root_cause,
        corrective_action=corrective_action,
        severity=severity,
        confidence=confidence,
        entity_refs=_json_arg(entity_refs, []),
        evidence_refs=_json_arg(evidence_refs, []),
        privacy_level=privacy_level,
        idempotency_key=idempotency_key,
        metadata=_json_arg(metadata, {}),
    )
    return _dump(result)


def handle_failure_prevention_cases(failure_uid: str = "", status: str = "", limit: int = 20, surface: str = "audit") -> str:
    """List failure prevention cases or return one case by uid."""
    if failure_uid:
        return _dump(get_failure_case(failure_uid, surface=surface))
    return _dump({"cases": list_failure_cases(status=status, limit=limit, surface=surface)})


def handle_failure_source_validate(source_type: str, source_ref: str) -> str:
    """Validate a failure source reference without mutating the ledger."""
    return _dump(validate_source_ref(source_type, source_ref))


def handle_antibody_propose(
    failure_uid: str,
    action_type: str,
    target_system: str,
    target_ref: str,
    action_payload_ref: str = "",
    activation_policy: str = "candidate_only",
    required_verification: str = "",
    verification_ref: str = "",
    verification_status: str = "missing",
    approved_by: str = "",
    approved_ref: str = "",
    rollback_ref: str = "",
    privacy_level: str = "normal",
    metadata: str = "{}",
) -> str:
    """Record a proposed antibody action without executing it."""
    result = propose_antibody_action(
        failure_uid=failure_uid,
        action_type=action_type,
        target_system=target_system,
        target_ref=target_ref,
        action_payload_ref=action_payload_ref,
        activation_policy=activation_policy,
        required_verification=required_verification,
        verification_ref=verification_ref,
        verification_status=verification_status,
        approved_by=approved_by,
        approved_ref=approved_ref,
        rollback_ref=rollback_ref,
        privacy_level=privacy_level,
        metadata=_json_arg(metadata, {}),
    )
    return _dump(result)


def handle_failure_prevention_false_positive(failure_uid: str, antibody_uid: str = "", reason: str = "") -> str:
    """Mark a failure case or antibody as a false positive signal."""
    return _dump(mark_false_positive(failure_uid, antibody_uid=antibody_uid, reason=reason))


def handle_antibody_rollback(antibody_uid: str, rollback_ref: str, reason: str = "") -> str:
    """Mark an antibody action as rolled back and inactive."""
    return _dump(rollback_antibody_action(antibody_uid, rollback_ref=rollback_ref, reason=reason))


TOOLS = [
    (
        handle_failure_prevention_ingest,
        "nexo_failure_prevention_ingest",
        "Create or reinforce a redacted non-authoritative failure prevention case",
    ),
    (
        handle_failure_prevention_cases,
        "nexo_failure_prevention_cases",
        "List failure prevention cases or inspect one case by uid",
    ),
    (
        handle_failure_source_validate,
        "nexo_failure_source_validate",
        "Validate a failure source reference without mutating state",
    ),
    (
        handle_antibody_propose,
        "nexo_antibody_propose",
        "Record an antibody action proposal without executing it",
    ),
    (
        handle_failure_prevention_false_positive,
        "nexo_failure_prevention_false_positive",
        "Mark a failure prevention case or antibody as a false positive",
    ),
    (
        handle_antibody_rollback,
        "nexo_antibody_rollback",
        "Mark an antibody proposal as rolled back and inactive",
    ),
]
