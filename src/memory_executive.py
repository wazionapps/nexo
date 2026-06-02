"""Memory Executive policy layer.

This module is intentionally pure: it chooses the existing memory destination
for a write candidate and returns an auditable decision. It does not create a
new store and it does not perform writes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


VALID_PRIVACY_LEVELS = {"public", "normal", "private", "sensitive", "secret"}
PRIVACY_ALIASES = {"internal": "normal", "confidential": "sensitive"}

VALID_DECISION_KINDS = {
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
}
VALID_DESTINATIONS = {
    "none",
    "recent_context",
    "memory_events",
    "memory_observation_queue",
    "commitments",
    "workflow_runs",
    "preferences",
    "entities",
    "cognitive_quarantine",
    "learning_candidate",
    "risk_autopsy_candidate",
    "causal_edge_candidates",
}
VALID_WRITE_MODES = {"none", "create", "update", "reinforce", "review", "link_only"}
VALID_PRE_ANSWER_POLICIES = {"never", "cached_hint_only", "allowed_if_explicit", "allowed"}
VALID_REDACTION_POLICIES = {"none", "minimize", "redact", "reference_only", "drop"}
VALID_VERIFICATION_POLICIES = {"none", "deep_sleep_review", "conflict_review", "manual_review"}
VALID_CONFLICT_POLICIES = {"none", "do_not_overwrite", "quarantine", "supersede_with_evidence"}

SECRET_PATTERNS = (
    re.compile(
        r"\b(?:(?:sk|pk|rk)(?:[-_](?:live|test|proj))?[-_][A-Za-z0-9_=-]{10,}|"
        r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
        r"(?:xoxb|xoxp)-[A-Za-z0-9_=-]{10,})\b"
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
    re.compile(
        r"\b(api[_-]?key|token|secret|password|passwd|pwd|authorization)\s*[:=]\s*['\"]?[^'\"\s,;]+",
        re.IGNORECASE,
    ),
)
WORD_RE = re.compile(r"[a-z0-9_]+")

GENERAL_NOISE_MARKERS = {
    "que hora es",
    "what is 2 2",
    "2 2",
    "did it rain yesterday",
    "va a llover",
    "hola",
    "hello",
}
COMMITMENT_MARKERS = (
    "voy a",
    "hare",
    "i will",
    "will do",
    "prometo",
    "promise",
    "me comprometo",
    "commit to",
)
DECISION_MARKERS = ("decidimos", "decidi", "decided", "because", "porque", "por que")
RISK_MARKERS = ("falle", "failed", "error", "bug", "riesgo", "risk", "asumi", "assumed")
PREFERENCE_MARKERS = ("prefiero", "prefer", "quiero que", "me gusta que", "preference")
ENTITY_MARKERS = ("persona", "project", "proyecto", "cliente", "entity", "entidad")
LEARNING_MARKERS = ("aprendizaje", "learning", "correction", "correccion", "corrige", "reusable")


def normalize_privacy_level(value: str | None) -> str:
    clean = str(value or "").strip().lower()
    if not clean:
        return "private"
    clean = PRIVACY_ALIASES.get(clean, clean)
    return clean if clean in VALID_PRIVACY_LEVELS else "private"


def redact_text(value: str) -> tuple[str, bool]:
    redacted = str(value or "")
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    return redacted, redacted != str(value or "")


def _stable_hash(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        encoded = str(value)
    return hashlib.sha256(encoded.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _norm(value: str) -> str:
    text = str(value or "").lower()
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())


def _tokens(value: str) -> set[str]:
    return {item for item in WORD_RE.findall(_norm(value)) if len(item) >= 3}


def _metadata_text(metadata: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "summary",
        "statement",
        "goal",
        "outcome",
        "reason",
        "why",
        "risk",
        "preference",
        "entity_name",
        "canonical_name",
    ):
        value = metadata.get(key)
        if value not in (None, ""):
            parts.append(str(value))
    return " ".join(parts)


@dataclass(frozen=True)
class EventContract:
    event_uid: str
    source_type: str
    source_id: str
    event_type: str
    actor: str
    session_id: str
    project_key: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    privacy_level: str = "private"
    idempotency_key: str = ""
    created_at: str = ""

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "EventContract":
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        evidence_refs = payload.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            evidence_refs = []
        return cls(
            event_uid=str(payload.get("event_uid") or "").strip(),
            source_type=str(payload.get("source_type") or "").strip().lower(),
            source_id=str(payload.get("source_id") or "").strip(),
            event_type=str(payload.get("event_type") or "").strip().lower(),
            actor=str(payload.get("actor") or "").strip(),
            session_id=str(payload.get("session_id") or "").strip(),
            project_key=str(payload.get("project_key") or "").strip(),
            text=str(payload.get("text") or "").strip(),
            metadata=dict(metadata),
            evidence_refs=[str(ref).strip() for ref in evidence_refs if str(ref).strip()],
            privacy_level=normalize_privacy_level(payload.get("privacy_level")),
            idempotency_key=str(payload.get("idempotency_key") or "").strip(),
            created_at=str(payload.get("created_at") or "").strip(),
        )

    def combined_text(self) -> str:
        return " ".join(part for part in (self.text, _metadata_text(self.metadata)) if part).strip()


@dataclass(frozen=True)
class MemoryDecision:
    decision_kind: str
    destination: str
    owner_module: str
    write_mode: str
    confidence: float
    reason: str
    dedupe_key: str
    ttl_or_decay: str
    privacy_level: str
    redaction_policy: str
    pre_answer_policy: str
    verification_policy: str
    conflict_policy: str
    side_effects: list[str]
    max_latency_ms: int
    requires_shadow_mode: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_kind": self.decision_kind,
            "destination": self.destination,
            "owner_module": self.owner_module,
            "write_mode": self.write_mode,
            "confidence": round(max(0.0, min(1.0, float(self.confidence))), 4),
            "reason": self.reason,
            "dedupe_key": self.dedupe_key,
            "ttl_or_decay": self.ttl_or_decay,
            "privacy_level": self.privacy_level,
            "redaction_policy": self.redaction_policy,
            "pre_answer_policy": self.pre_answer_policy,
            "verification_policy": self.verification_policy,
            "conflict_policy": self.conflict_policy,
            "side_effects": list(self.side_effects),
            "max_latency_ms": int(self.max_latency_ms),
            "requires_shadow_mode": bool(self.requires_shadow_mode),
        }


def _make_decision(
    *,
    event: EventContract,
    decision_kind: str,
    destination: str,
    owner_module: str,
    write_mode: str,
    confidence: float,
    reason: str,
    dedupe_key: str,
    ttl_or_decay: str = "default",
    privacy_level: str | None = None,
    redaction_policy: str = "minimize",
    pre_answer_policy: str = "cached_hint_only",
    verification_policy: str = "none",
    conflict_policy: str = "none",
    side_effects: list[str] | None = None,
    max_latency_ms: int = 40,
    requires_shadow_mode: bool = True,
) -> MemoryDecision:
    clean_privacy = normalize_privacy_level(privacy_level or event.privacy_level)
    if clean_privacy == "secret":
        redaction_policy = "drop"
        pre_answer_policy = "never"
        verification_policy = "manual_review"
        conflict_policy = "quarantine"
    elif clean_privacy == "sensitive":
        redaction_policy = "reference_only" if redaction_policy in {"none", "minimize"} else redaction_policy
        pre_answer_policy = "allowed_if_explicit" if pre_answer_policy == "allowed" else pre_answer_policy
        verification_policy = "manual_review" if verification_policy == "none" else verification_policy

    decision = MemoryDecision(
        decision_kind=decision_kind,
        destination=destination,
        owner_module=owner_module,
        write_mode=write_mode,
        confidence=confidence,
        reason=reason,
        dedupe_key=dedupe_key,
        ttl_or_decay=ttl_or_decay,
        privacy_level=clean_privacy,
        redaction_policy=redaction_policy,
        pre_answer_policy=pre_answer_policy,
        verification_policy=verification_policy,
        conflict_policy=conflict_policy,
        side_effects=side_effects or [],
        max_latency_ms=max_latency_ms,
        requires_shadow_mode=requires_shadow_mode,
    )
    validate_decision(decision)
    return decision


def validate_decision(decision: MemoryDecision) -> None:
    if decision.decision_kind not in VALID_DECISION_KINDS:
        raise ValueError(f"invalid decision_kind: {decision.decision_kind}")
    if decision.destination not in VALID_DESTINATIONS:
        raise ValueError(f"invalid destination: {decision.destination}")
    if decision.write_mode not in VALID_WRITE_MODES:
        raise ValueError(f"invalid write_mode: {decision.write_mode}")
    if decision.privacy_level not in VALID_PRIVACY_LEVELS:
        raise ValueError(f"invalid privacy_level: {decision.privacy_level}")
    if decision.redaction_policy not in VALID_REDACTION_POLICIES:
        raise ValueError(f"invalid redaction_policy: {decision.redaction_policy}")
    if decision.pre_answer_policy not in VALID_PRE_ANSWER_POLICIES:
        raise ValueError(f"invalid pre_answer_policy: {decision.pre_answer_policy}")
    if decision.verification_policy not in VALID_VERIFICATION_POLICIES:
        raise ValueError(f"invalid verification_policy: {decision.verification_policy}")
    if decision.conflict_policy not in VALID_CONFLICT_POLICIES:
        raise ValueError(f"invalid conflict_policy: {decision.conflict_policy}")


def _is_general_noise(text: str, event: EventContract) -> bool:
    normalized = _norm(text)
    if event.event_type in {"question", "chat"} and any(marker in normalized for marker in GENERAL_NOISE_MARKERS):
        return True
    if normalized in GENERAL_NOISE_MARKERS:
        return True
    tokens = _tokens(normalized)
    return bool(tokens) and tokens <= {"what", "hora", "rain", "yesterday", "hello", "hola"}


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    normalized = _norm(text)
    return any(_norm(marker) in normalized for marker in markers)


def _entity_key(event: EventContract) -> str:
    kind = str(event.metadata.get("entity_kind") or event.metadata.get("kind") or "entity").strip().lower()
    canonical = str(
        event.metadata.get("canonical_name")
        or event.metadata.get("entity_name")
        or event.metadata.get("name")
        or _stable_hash(event.combined_text())
    ).strip().lower()
    canonical = "-".join(WORD_RE.findall(canonical)) or _stable_hash(event.combined_text())
    return f"entity:{kind}:{canonical}:{event.source_type}:{event.source_id}"


def decide(event: EventContract | dict[str, Any], *, shadow_mode: bool = True) -> dict[str, Any]:
    contract = event if isinstance(event, EventContract) else EventContract.from_mapping(event)
    text = contract.combined_text()
    _, secret_found = redact_text(text)
    event_uid = contract.event_uid
    idempotency_key = contract.idempotency_key
    active_safe = bool(event_uid and idempotency_key)

    if contract.privacy_level == "secret" or secret_found:
        return _make_decision(
            event=contract,
            decision_kind="quarantine",
            destination="cognitive_quarantine",
            owner_module="cognitive._ingest",
            write_mode="review",
            confidence=0.98,
            reason="Secret-like payload must not be stored as normal memory.",
            dedupe_key=f"quarantine:{event_uid or _stable_hash(text)}",
            ttl_or_decay="manual_review",
            privacy_level="secret",
            redaction_policy="drop",
            pre_answer_policy="never",
            verification_policy="manual_review",
            conflict_policy="quarantine",
            max_latency_ms=20,
            requires_shadow_mode=shadow_mode,
        ).to_dict()

    if not active_safe:
        return _make_decision(
            event=contract,
            decision_kind="ignore",
            destination="none",
            owner_module="memory_executive",
            write_mode="none",
            confidence=0.95,
            reason="Missing event_uid or idempotency_key; active writes are unsafe.",
            dedupe_key=event_uid or idempotency_key or f"unsafe:{_stable_hash(text)}",
            ttl_or_decay="none",
            redaction_policy="minimize",
            pre_answer_policy="never",
            verification_policy="manual_review",
            conflict_policy="do_not_overwrite",
            max_latency_ms=20,
            requires_shadow_mode=True,
        ).to_dict()

    if _is_general_noise(text, contract):
        return _make_decision(
            event=contract,
            decision_kind="ignore",
            destination="none",
            owner_module="memory_executive",
            write_mode="none",
            confidence=0.97,
            reason="General low-continuity query should not create memory.",
            dedupe_key=event_uid,
            ttl_or_decay="none",
            redaction_policy="none",
            pre_answer_policy="never",
            max_latency_ms=20,
            requires_shadow_mode=shadow_mode,
        ).to_dict()

    if contract.source_type == "deep_sleep" and (
        contract.metadata.get("duplicate_of") or contract.metadata.get("existing_event_uid")
    ):
        return _make_decision(
            event=contract,
            decision_kind="learning_candidate",
            destination="memory_observation_queue",
            owner_module="db._memory_v2",
            write_mode="reinforce",
            confidence=0.9,
            reason="Deep Sleep duplicate should reinforce/review the existing observation, not create a parallel memory.",
            dedupe_key=f"memory_observation_queue:{contract.metadata.get('existing_event_uid') or event_uid}",
            ttl_or_decay="decay_existing",
            verification_policy="deep_sleep_review",
            conflict_policy="do_not_overwrite",
            side_effects=["queue_reinforce"],
            requires_shadow_mode=shadow_mode,
        ).to_dict()

    if contract.event_type in {"workflow_update", "workflow_checkpoint"} or contract.metadata.get("workflow_run_id"):
        return _make_decision(
            event=contract,
            decision_kind="workflow_checkpoint",
            destination="workflow_runs",
            owner_module="db._workflow",
            write_mode="update",
            confidence=0.93,
            reason="Durable workflow progress belongs to workflow_runs via idempotency_key.",
            dedupe_key=f"workflow_runs:{contract.session_id}:{idempotency_key}",
            ttl_or_decay="durable_until_closed",
            side_effects=["workflow_checkpoint"],
            requires_shadow_mode=shadow_mode,
        ).to_dict()

    if contract.event_type in {"commitment", "promise", "future_action"} or _contains_any(text, COMMITMENT_MARKERS):
        return _make_decision(
            event=contract,
            decision_kind="commitment",
            destination="commitments",
            owner_module="db._commitments",
            write_mode="create",
            confidence=0.9,
            reason="Future-action promise belongs to the commitment ledger; calendar surfaces require explicit scheduling.",
            dedupe_key=f"commitments:{idempotency_key}",
            ttl_or_decay="open_until_fulfilled_or_cancelled",
            pre_answer_policy="allowed_if_explicit",
            conflict_policy="do_not_overwrite",
            side_effects=["commitment_ledger"],
            requires_shadow_mode=shadow_mode,
        ).to_dict()

    if contract.event_type in {"preference", "preference_update"} or _contains_any(text, PREFERENCE_MARKERS):
        inferred = str(contract.metadata.get("preference_kind") or "").strip().lower() == "inferred"
        conflict = bool(contract.metadata.get("conflicts_with"))
        key = str(contract.metadata.get("preference_key") or _stable_hash(text)).strip().lower()
        return _make_decision(
            event=contract,
            decision_kind="preference",
            destination="preferences",
            owner_module="db._entities",
            write_mode="review" if inferred or conflict else "update",
            confidence=0.88 if not inferred else 0.62,
            reason="Explicit preferences may persist; inferred or conflicting preferences require review.",
            dedupe_key=f"preferences:{key}",
            ttl_or_decay="stable_until_changed",
            pre_answer_policy="allowed",
            verification_policy="conflict_review" if inferred or conflict else "none",
            conflict_policy="do_not_overwrite" if inferred or conflict else "supersede_with_evidence",
            side_effects=["preference_update"] if not inferred and not conflict else ["preference_review"],
            requires_shadow_mode=shadow_mode,
        ).to_dict()

    if contract.event_type in {"entity_update", "entity"} or contract.metadata.get("entity_name") or _contains_any(text, ENTITY_MARKERS):
        conflict = bool(contract.metadata.get("conflicts_with"))
        return _make_decision(
            event=contract,
            decision_kind="entity_update",
            destination="entities",
            owner_module="db._entities",
            write_mode="review" if conflict or contract.privacy_level in {"sensitive", "private"} else "update",
            confidence=0.84,
            reason="Entity facts use the existing entity store and conflicts require review.",
            dedupe_key=_entity_key(contract),
            ttl_or_decay="entity_profile_decay",
            pre_answer_policy="allowed_if_explicit" if contract.privacy_level in {"private", "sensitive"} else "allowed",
            verification_policy="conflict_review" if conflict else "manual_review",
            conflict_policy="do_not_overwrite" if conflict else "none",
            side_effects=["entity_review" if conflict else "entity_update"],
            requires_shadow_mode=shadow_mode,
        ).to_dict()

    if contract.event_type in {"causal_observation", "causal_candidate"} or contract.metadata.get("causal_relation"):
        relation = str(contract.metadata.get("causal_key") or idempotency_key).strip()
        return _make_decision(
            event=contract,
            decision_kind="proposed_causal_edge",
            destination="causal_edge_candidates",
            owner_module="causal_graph",
            write_mode="review",
            confidence=0.82,
            reason="Causal facts are emitted as candidates for the causal graph spec, not persisted here.",
            dedupe_key=f"causal_edge_candidates:{relation}",
            ttl_or_decay="candidate_review",
            pre_answer_policy="cached_hint_only",
            verification_policy="manual_review",
            conflict_policy="do_not_overwrite",
            side_effects=["causal_candidate"],
            requires_shadow_mode=shadow_mode,
        ).to_dict()

    if contract.event_type in {"risk", "failure", "error"} or _contains_any(text, RISK_MARKERS):
        return _make_decision(
            event=contract,
            decision_kind="risk_signal",
            destination="risk_autopsy_candidate",
            owner_module="failure_prevention",
            write_mode="review",
            confidence=0.86,
            reason="Failure/risk signal should become an autopsy candidate with evidence.",
            dedupe_key=f"risk_autopsy_candidate:{idempotency_key}",
            ttl_or_decay="review_then_decay",
            pre_answer_policy="cached_hint_only",
            verification_policy="manual_review",
            conflict_policy="do_not_overwrite",
            side_effects=["autopsy_candidate"],
            requires_shadow_mode=shadow_mode,
        ).to_dict()

    if contract.event_type in {"learning", "correction"} or _contains_any(text, LEARNING_MARKERS):
        return _make_decision(
            event=contract,
            decision_kind="learning_candidate",
            destination="memory_observation_queue",
            owner_module="db._memory_v2",
            write_mode="review",
            confidence=0.84,
            reason="Learning candidates go through the existing observation queue before promotion.",
            dedupe_key=f"memory_observation_queue:{event_uid}",
            ttl_or_decay="review_then_decay",
            pre_answer_policy="cached_hint_only",
            verification_policy="deep_sleep_review",
            conflict_policy="do_not_overwrite",
            side_effects=["observation_queue"],
            requires_shadow_mode=shadow_mode,
        ).to_dict()

    if contract.event_type in {"decision", "task_close"} or _contains_any(text, DECISION_MARKERS):
        return _make_decision(
            event=contract,
            decision_kind="decision",
            destination="memory_events",
            owner_module="db._memory_v2",
            write_mode="create",
            confidence=0.86,
            reason="Decision/rationale should be recorded as an append-only memory_event with evidence refs.",
            dedupe_key=f"memory_events:{event_uid}",
            ttl_or_decay="append_only",
            pre_answer_policy="cached_hint_only",
            side_effects=["append_memory_event"],
            requires_shadow_mode=shadow_mode,
        ).to_dict()

    if contract.evidence_refs or contract.event_type == "evidence_ref":
        return _make_decision(
            event=contract,
            decision_kind="evidence_ref",
            destination="memory_events",
            owner_module="db._memory_v2",
            write_mode="link_only",
            confidence=0.78,
            reason="Evidence is referenced rather than copied into a new evidence store.",
            dedupe_key=f"evidence:{contract.source_type}:{contract.source_id}",
            ttl_or_decay="source_owned",
            pre_answer_policy="cached_hint_only",
            side_effects=["evidence_link"],
            requires_shadow_mode=shadow_mode,
        ).to_dict()

    return _make_decision(
        event=contract,
        decision_kind="recent_context",
        destination="recent_context",
        owner_module="db._hot_context",
        write_mode="create",
        confidence=0.72,
        reason="Operational context is useful short-term but not yet durable memory.",
        dedupe_key=f"recent_context:{contract.session_id}:{event_uid}",
        ttl_or_decay="24h",
        pre_answer_policy="cached_hint_only",
        side_effects=["hot_context"],
        requires_shadow_mode=shadow_mode,
    ).to_dict()


def audit_record(event: EventContract | dict[str, Any], decision: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = event if isinstance(event, EventContract) else EventContract.from_mapping(event)
    output = dict(decision or decide(contract))
    return {
        "schema": "memory_executive.audit.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "event_uid": contract.event_uid,
        "source_type": contract.source_type,
        "source_id": contract.source_id,
        "event_type": contract.event_type,
        "privacy_level": output.get("privacy_level"),
        "decision_kind": output.get("decision_kind"),
        "destination": output.get("destination"),
        "owner_module": output.get("owner_module"),
        "write_mode": output.get("write_mode"),
        "dedupe_key": output.get("dedupe_key"),
        "redaction_policy": output.get("redaction_policy"),
        "pre_answer_policy": output.get("pre_answer_policy"),
        "verification_policy": output.get("verification_policy"),
        "conflict_policy": output.get("conflict_policy"),
        "reason": output.get("reason"),
        "evidence_ref_count": len(contract.evidence_refs),
    }


__all__ = [
    "EventContract",
    "MemoryDecision",
    "audit_record",
    "decide",
    "normalize_privacy_level",
    "redact_text",
    "validate_decision",
]
