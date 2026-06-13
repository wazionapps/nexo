"""Runtime wrapper for pre-answer routing across CLI, MCP and Desktop."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from pre_answer_router import DEFAULT_BUDGET_MS, DEFAULT_TOKEN_BUDGET, classify_intent, route_pre_answer


RUNTIME_BUDGET_POLICY_VERSION = "runtime_budget_v1"

HEAVY_SOURCES = {"memory", "cognitive", "local_context", "transcripts"}
CANONICAL_ROUTER_SOURCES = {
    "semantic_layers",
    "recent_context",
    "evidence_ledger",
    "commitments",
    "protocol_tasks",
    "workflows",
    "change_log",
    "causal_graph",
    "diary",
    "transcripts",
    "memory",
    "local_context",
    "project_atlas",
    "filesystem",
    "guard_context",
    "reminders",
    "followups",
    "cognitive",
    "continuity",
    "system_catalog",
    "runtime_docs",
    "source_grep",
    "runtime_db",
}


@dataclass(frozen=True)
class RuntimeBudgetPolicy:
    ok: bool
    budget_decision_uid: str
    policy_version: str
    surface: str
    budget_tier: str
    intent: str
    risk_level: str
    operational_state: dict[str, Any] = field(default_factory=dict)
    deadline_ms: int = DEFAULT_BUDGET_MS
    first_response_deadline_ms: int = 700
    token_budget: int = DEFAULT_TOKEN_BUDGET
    max_rendered_chars: int = 3000
    max_sources: int = 5
    max_source_timeout_ms: int = 500
    allowed_sources: tuple[str, ...] = ()
    forbidden_sources: tuple[str, ...] = ()
    required_sources: tuple[str, ...] = ()
    required_checks: tuple[str, ...] = ()
    fallback_policy: str = "fallback_if_no_evidence"
    escalation_policy: str = "none"
    can_use_local_semantic: bool = True
    can_use_remote_llm: bool = False
    can_delay_first_response: bool = False
    must_disclose_gap: bool = False
    delay_message_threshold_ms: int = 0
    cache_ttl_seconds: int = 180
    route_cache_key: str = ""
    privacy_level: str = "normal"
    reason_codes: tuple[str, ...] = ()
    escalated_from: str = ""
    escalated_to: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("allowed_sources", "forbidden_sources", "required_sources", "required_checks", "reason_codes"):
            payload[key] = list(payload.get(key) or [])
        return payload


def _as_optional_positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except Exception:
        return None
    return number if number > 0 else None


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()


def _route_cache_key(
    *,
    surface: str,
    intent: str,
    query: str,
    area: str,
    files: str,
    risk_level: str,
    policy_version: str,
) -> str:
    return _hash_json(
        {
            "surface": surface,
            "intent": intent,
            "query_hash": hashlib.sha256(str(query or "").encode("utf-8", errors="ignore")).hexdigest(),
            "area": area,
            "files_hash": hashlib.sha256(str(files or "").encode("utf-8", errors="ignore")).hexdigest() if files else "",
            "risk_level": risk_level,
            "policy_version": policy_version,
        }
    )


def _canonical_sources(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(source for source in values if source in CANONICAL_ROUTER_SOURCES)


def _tier_spec(tier: str) -> dict[str, Any]:
    if tier == "instant":
        return {
            "deadline_ms": 80,
            "first_response_deadline_ms": 80,
            "token_budget": 0,
            "max_rendered_chars": 0,
            "max_sources": 0,
            "max_source_timeout_ms": 0,
            "allowed_sources": (),
            "forbidden_sources": tuple(sorted(HEAVY_SOURCES | {"remote_llm"})),
            "required_sources": (),
            "fallback_policy": "none",
            "escalation_policy": "none",
            "cache_ttl_seconds": 0,
        }
    if tier == "quick":
        return {
            "deadline_ms": 300,
            "first_response_deadline_ms": 300,
            "token_budget": 600,
            "max_rendered_chars": 900,
            "max_sources": 2,
            "max_source_timeout_ms": 140,
            "allowed_sources": ("semantic_layers", "project_atlas", "system_catalog", "recent_context"),
            "forbidden_sources": tuple(sorted(HEAVY_SOURCES | {"remote_llm"})),
            "required_sources": (),
            "fallback_policy": "primary_only",
            "escalation_policy": "one_step_if_no_evidence",
            "cache_ttl_seconds": 300,
        }
    if tier == "deep":
        return {
            "deadline_ms": 5000,
            "first_response_deadline_ms": 1200,
            "token_budget": 4500,
            "max_rendered_chars": 9000,
            "max_sources": 9,
            "max_source_timeout_ms": 1200,
            "allowed_sources": tuple(sorted(CANONICAL_ROUTER_SOURCES)),
            "forbidden_sources": (),
            "required_sources": (),
            "fallback_policy": "fallback_if_no_evidence",
            "escalation_policy": "one_step_if_no_evidence",
            "cache_ttl_seconds": 90,
            "can_delay_first_response": True,
            "delay_message_threshold_ms": 1200,
        }
    if tier == "critical":
        return {
            "deadline_ms": 20000,
            "first_response_deadline_ms": 1500,
            "token_budget": 7000,
            "max_rendered_chars": 14000,
            "max_sources": 12,
            "max_source_timeout_ms": 2500,
            "allowed_sources": tuple(sorted(CANONICAL_ROUTER_SOURCES)),
            "forbidden_sources": ("remote_llm",),
            "required_sources": ("guard_context", "project_atlas", "evidence_ledger"),
            "required_checks": ("guard", "atlas", "evidence_ledger"),
            "fallback_policy": "mandatory_fail_closed",
            "escalation_policy": "defer_if_required_missing",
            "cache_ttl_seconds": 30,
            "can_delay_first_response": True,
            "must_disclose_gap": True,
            "delay_message_threshold_ms": 1500,
            "privacy_level": "private",
        }
    return {
        "deadline_ms": 1200,
        "first_response_deadline_ms": 700,
        "token_budget": 1800,
        "max_rendered_chars": 3000,
        "max_sources": 5,
        "max_source_timeout_ms": 500,
        "allowed_sources": (
            "semantic_layers",
            "commitments",
            "reminders",
            "followups",
            "recent_context",
            "project_atlas",
            "filesystem",
            "change_log",
            "evidence_ledger",
            "protocol_tasks",
            "workflows",
            "causal_graph",
            "memory",
            "transcripts",
        ),
        "forbidden_sources": ("remote_llm",),
        "required_sources": (),
        "fallback_policy": "fallback_if_no_evidence",
        "escalation_policy": "one_step_if_no_evidence",
        "cache_ttl_seconds": 180,
    }


def _base_tier_for(
    *,
    intent: str,
    area: str,
    risk_level: str,
    operational_state: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    clean_area = _clean(area)
    op_caution = _clean(operational_state.get("caution_level"))
    op_risk = _clean(operational_state.get("area_risk"))
    op_verification = _clean(operational_state.get("verification_requirement"))
    if (
        risk_level in {"high", "critical"}
        or op_caution == "max_caution"
        or op_risk == "critical"
        or op_verification == "release_gate"
        or clean_area in {"release", "server", "billing", "legal", "external_publication"}
    ):
        reasons.append("critical_boundary")
        return "critical", reasons
    if intent == "general":
        reasons.append("general_no_action")
        return "instant", reasons
    if intent == "file_location":
        reasons.append("simple_file_location")
        return "quick", reasons
    if intent == "live_state_claim":
        reasons.append("live_state_claim_deep")
        return "deep", reasons
    if intent in {"runtime_diagnosis"}:
        reasons.append("runtime_diagnosis_deep")
        return "deep", reasons
    if intent in {"prior_work", "identity_authorship"} and clean_area in {"brain", "desktop", "release", "server"}:
        reasons.append("operational_history_deep")
        return "deep", reasons
    if intent in {"schedule_commitment", "memory_question", "prior_work", "identity_authorship", "modify_existing"}:
        reasons.append("continuity_standard")
        return "standard", reasons
    reasons.append("default_standard")
    return "standard", reasons


def _next_tier(tier: str) -> str:
    order = ("instant", "quick", "standard", "deep", "critical")
    try:
        idx = order.index(tier)
    except ValueError:
        return "standard"
    return order[min(idx + 1, len(order) - 1)]


def select_budget_policy(
    *,
    query: str,
    surface: str = "pre_answer",
    intent: str = "general",
    area: str = "",
    files: str = "",
    risk_level: str = "",
    operational_state: dict[str, Any] | None = None,
    budget_ms_override: int | None = None,
    token_budget_override: int | None = None,
    force_tier: str = "",
    escalated_from: str = "",
) -> RuntimeBudgetPolicy:
    surface = _clean(surface) or "pre_answer"
    intent = _clean(intent) or "general"
    risk_level = _clean(risk_level) or "low"
    operational_state = dict(operational_state or {})
    tier, reasons = _base_tier_for(
        intent=intent,
        area=area,
        risk_level=risk_level,
        operational_state=operational_state,
    )
    if force_tier:
        forced = _clean(force_tier)
        if forced in {"instant", "quick", "standard", "deep", "critical"}:
            tier = forced
            reasons.append("tier_escalated" if escalated_from else "tier_forced")
    spec = _tier_spec(tier)
    if escalated_from == "quick" and tier == "standard":
        spec = dict(spec)
        spec["allowed_sources"] = tuple(
            source for source in tuple(spec.get("allowed_sources") or ()) if source not in HEAVY_SOURCES
        )
        spec["forbidden_sources"] = tuple(sorted(set(tuple(spec.get("forbidden_sources") or ())) | HEAVY_SOURCES))
        reasons.append("quick_escalation_heavy_sources_blocked")
    required_checks = list(spec.get("required_checks") or ())
    clean_area = _clean(area)
    op_verification = _clean(operational_state.get("verification_requirement"))
    if tier == "critical":
        if clean_area == "release" or op_verification == "release_gate":
            required_checks.append("release_readiness")
        if clean_area == "server":
            required_checks.append("server_verification")
        if clean_area in {"billing", "legal", "external_publication"}:
            required_checks.append("permissions")
    deadline_ms = int(spec["deadline_ms"])
    token_budget = int(spec["token_budget"])
    if budget_ms_override is not None:
        deadline_ms = int(budget_ms_override)
        reasons.append("deadline_override")
    if token_budget_override is not None:
        token_budget = int(token_budget_override)
        reasons.append("token_budget_override")
    route_key = _route_cache_key(
        surface=surface,
        intent=intent,
        query=query,
        area=area,
        files=files,
        risk_level=risk_level,
        policy_version=RUNTIME_BUDGET_POLICY_VERSION,
    )
    escalated_to = tier if escalated_from else ""
    uid = _hash_json(
        {
            "policy_version": RUNTIME_BUDGET_POLICY_VERSION,
            "surface": surface,
            "intent": intent,
            "risk_level": risk_level,
            "budget_tier": tier,
            "route_cache_key": route_key,
        }
    )
    return RuntimeBudgetPolicy(
        ok=True,
        budget_decision_uid=uid,
        policy_version=RUNTIME_BUDGET_POLICY_VERSION,
        surface=surface,
        budget_tier=tier,
        intent=intent,
        risk_level=risk_level,
        operational_state=operational_state,
        deadline_ms=deadline_ms,
        first_response_deadline_ms=int(spec["first_response_deadline_ms"]),
        token_budget=token_budget,
        max_rendered_chars=int(spec["max_rendered_chars"]),
        max_sources=int(spec["max_sources"]),
        max_source_timeout_ms=int(spec["max_source_timeout_ms"]),
        allowed_sources=_canonical_sources(tuple(spec.get("allowed_sources") or ())),
        forbidden_sources=tuple(spec.get("forbidden_sources") or ()),
        required_sources=_canonical_sources(tuple(spec.get("required_sources") or ())),
        required_checks=tuple(dict.fromkeys(required_checks)),
        fallback_policy=str(spec.get("fallback_policy") or "none"),
        escalation_policy=str(spec.get("escalation_policy") or "none"),
        can_use_local_semantic=tier != "instant",
        can_use_remote_llm=False,
        can_delay_first_response=bool(spec.get("can_delay_first_response", False)),
        must_disclose_gap=bool(spec.get("must_disclose_gap", False)),
        delay_message_threshold_ms=int(spec.get("delay_message_threshold_ms") or 0),
        cache_ttl_seconds=int(spec.get("cache_ttl_seconds") or 0),
        route_cache_key=route_key,
        privacy_level=str(spec.get("privacy_level") or "normal"),
        reason_codes=tuple(dict.fromkeys(reasons)),
        escalated_from=escalated_from,
        escalated_to=escalated_to,
    )


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = str(block.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()


def extract_query(payload: dict[str, Any]) -> str:
    """Return the user-visible text the router should classify."""

    for key in ("query", "text", "message"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return _text_from_content(payload.get("content"))


def _run_with_policy(
    *,
    query: str,
    payload: dict[str, Any],
    policy: RuntimeBudgetPolicy,
    classification: Any,
    source_adapters: dict[str, Any] | None,
    telemetry_sink: Any | None,
) -> dict[str, Any]:
    route = route_pre_answer(
        query,
        sid=str(payload.get("sid") or payload.get("session_id") or ""),
        conversation_id=str(payload.get("conversation_id") or payload.get("conversationId") or ""),
        intent=policy.intent,
        area=str(payload.get("area") or ""),
        files=str(payload.get("files") or ""),
        budget_ms=policy.deadline_ms,
        token_budget=policy.token_budget,
        current_context=str(payload.get("current_context") or payload.get("currentContext") or ""),
        source_adapters=source_adapters,
        telemetry_sink=telemetry_sink,
        budget_policy=policy.to_dict(),
        classification_override=classification,
    )
    result = route.to_dict()
    result["deadline_ms"] = policy.deadline_ms
    result["timed_out"] = result.get("aborted_reason") in {
        "timeout",
        "deadline_exhausted",
        "source_timeout",
        "required_source_timeout",
    }
    result["route_used"] = "brain_pre_answer_router"
    result["runtime_budget_policy"] = policy.to_dict()
    return result


def run_pre_answer_route(
    payload: dict[str, Any],
    *,
    source_adapters: dict[str, Any] | None = None,
    telemetry_sink: Any | None = None,
) -> dict[str, Any]:
    """Execute the semantic pre-answer router and persist lightweight usage telemetry."""

    query = extract_query(payload)
    if not query:
        return {
            "ok": False,
            "should_inject": False,
            "error": "query_required",
            "intent": str(payload.get("intent") or "auto"),
        }

    requested_intent = str(payload.get("intent") or "auto").strip() or "auto"
    classification = classify_intent(
        query,
        current_context=str(payload.get("current_context") or payload.get("currentContext") or ""),
    ) if requested_intent == "auto" else None
    resolved_intent = classification.intent if classification is not None else requested_intent
    policy = select_budget_policy(
        query=query,
        surface=str(payload.get("surface") or "pre_answer"),
        intent=resolved_intent,
        area=str(payload.get("area") or ""),
        files=str(payload.get("files") or ""),
        risk_level=str(payload.get("risk_level") or payload.get("riskLevel") or ""),
        operational_state=payload.get("operational_state") if isinstance(payload.get("operational_state"), dict) else {},
        budget_ms_override=_as_optional_positive_int(payload.get("budget_ms")),
        token_budget_override=_as_optional_positive_int(payload.get("token_budget")),
    )
    result = _run_with_policy(
        query=query,
        payload=payload,
        policy=policy,
        classification=classification,
        source_adapters=source_adapters,
        telemetry_sink=telemetry_sink,
    )
    if (
        not result.get("should_inject")
        and policy.escalation_policy == "one_step_if_no_evidence"
        and policy.budget_tier not in {"instant", "critical"}
    ):
        escalated_policy = select_budget_policy(
            query=query,
            surface=str(payload.get("surface") or "pre_answer"),
            intent=resolved_intent,
            area=str(payload.get("area") or ""),
            files=str(payload.get("files") or ""),
            risk_level=str(payload.get("risk_level") or payload.get("riskLevel") or ""),
            operational_state=payload.get("operational_state") if isinstance(payload.get("operational_state"), dict) else {},
            budget_ms_override=_as_optional_positive_int(payload.get("budget_ms")),
            token_budget_override=_as_optional_positive_int(payload.get("token_budget")),
            force_tier=_next_tier(policy.budget_tier),
            escalated_from=policy.budget_tier,
        )
        escalated = _run_with_policy(
            query=query,
            payload=payload,
            policy=escalated_policy,
            classification=classification,
            source_adapters=source_adapters,
            telemetry_sink=telemetry_sink,
        )
        if escalated.get("should_inject") or not result.get("evidence_refs"):
            result = escalated
        result["escalated_from"] = policy.budget_tier
        result["escalated_to"] = escalated_policy.budget_tier

    try:
        from local_context.usage_events import record_router_usage

        result["usage_event"] = record_router_usage(
            query,
            result,
            client=str(payload.get("source") or payload.get("client") or "unknown"),
            tool="pre_answer_router",
            route_stage="pre_answer",
            intent=str(result.get("intent") or payload.get("intent") or "auto"),
            elapsed_ms=int(float(result.get("elapsed_ms") or 0)),
            deadline_ms=int(result.get("deadline_ms") or policy.deadline_ms),
            used_before_response=True,
        )
    except Exception as exc:
        result["usage_event"] = {
            "ok": False,
            "error": "usage_event_failed",
            "detail": f"{type(exc).__name__}: {exc}",
        }

    return result
