"""Pre-answer semantic router for NEXO Brain.

This module is intentionally side-effect light: it classifies a user turn,
chooses the evidence sources that should be consulted before answering, runs
them inside a hard deadline, and returns a compact injectable bundle.

Shared entrypoints (MCP/CLI/Desktop) are wired elsewhere. G01 owns only this
core so integration can happen without editing server.py or cli.py here.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable


PRE_ANSWER_INTENTS: tuple[str, ...] = (
    "prior_work",
    "file_location",
    "modify_existing",
    "memory_question",
    "identity_authorship",
    "schedule_commitment",
    "live_state_claim",
    "runtime_diagnosis",
    "general",
)

INJECTING_INTENTS = set(PRE_ANSWER_INTENTS) - {"general"}
EVIDENCE_REQUIRED_INTENTS = {"live_state_claim"}

DEFAULT_BUDGET_MS = 2500
DEFAULT_TOKEN_BUDGET = 2500
STRONG_TRANSCRIPT_INDEX_MATCH = 0.75
MAX_SOURCE_WORKERS = int(os.environ.get("NEXO_PRE_ANSWER_SOURCE_WORKERS", "6") or "6")
PRE_ANSWER_SEMANTIC_DECISION_KIND = "pre_answer_intent"

_WORD_RE = re.compile(r"[a-z0-9_./:@-]+")
_PLAIN_WORD_RE = re.compile(r"[a-z0-9_]+")
_PATHISH_RE = re.compile(
    r"(?:(?:~|\.{1,2}|/)[\w./@+-]+|[\w.-]+\.(?:py|js|ts|tsx|jsx|md|json|db|sqlite|yml|yaml|txt|csv))"
)
_DATEISH_RE = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}-\d{2}-\d{2})\b")
_COLD_CONTINUITY_STEMS: tuple[str, ...] = (
    "promet",
    "promise",
    "commit",
    "compromis",
    "pendient",
    "pending",
    "followup",
    "deadline",
    "recordatorio",
    "reminder",
    "hice",
    "hiciste",
    "hicimos",
    "hecho",
    "toque",
    "toqu",
    "tocado",
    "touched",
)
_COLD_COMMITMENT_STEMS: tuple[str, ...] = (
    "promet",
    "promise",
    "commit",
    "compromis",
    "pendient",
    "pending",
    "followup",
    "deadline",
    "recordatorio",
    "reminder",
)
_COLD_OPERATOR_TOKENS: frozenset[str] = frozenset(
    {
        "i",
        "me",
        "my",
        "you",
        "your",
        "we",
        "our",
        "yo",
        "mi",
        "mis",
        "me",
        "tu",
        "tus",
        "nosotros",
        "nuestro",
        "nuestra",
        "nero",
    }
)
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:(?:sk|pk|rk)(?:[-_](?:live|test|proj))?[-_][A-Za-z0-9_=-]{10,}|"
            r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
            r"(?:xoxb|xoxp)-[A-Za-z0-9_=-]{10,})\b"
        ),
        "[REDACTED_SECRET]",
    ),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE), "Bearer [REDACTED_SECRET]"),
    (
        re.compile(
            r"\b(api[_-]?key|token|secret|password|passwd|pwd|authorization)\s*[:=]\s*['\"]?[^'\"\s,;]+",
            re.IGNORECASE,
        ),
        r"\1=[REDACTED_SECRET]",
    ),
)


# These are concept features, not phrase rules. The classifier scores overlap
# against multilingual semantic fields, then combines fields per intent.
_FEATURE_LEXICON: dict[str, tuple[str, ...]] = {
    "past_work": (
        "already",
        "before",
        "previous",
        "prior",
        "earlier",
        "yesterday",
        "last",
        "done",
        "did",
        "built",
        "fixed",
        "changed",
        "implemented",
        "shipped",
        "closed",
        "ya",
        "antes",
        "previo",
        "anterior",
        "ayer",
        "hecho",
        "hiciste",
        "hizo",
        "arreglaste",
        "cambiaste",
        "implementaste",
        "cerraste",
        "publicaste",
        "deja",
        "fait",
        "avant",
        "precedent",
        "vorher",
        "gemacht",
        "feito",
        "antes",
    ),
    "location": (
        "where",
        "locate",
        "location",
        "path",
        "file",
        "folder",
        "directory",
        "repo",
        "project",
        "artifact",
        "donde",
        "ruta",
        "archivo",
        "carpeta",
        "directorio",
        "repositorio",
        "proyecto",
        "artefacto",
        "localiza",
        "trouve",
        "emplacement",
        "fichier",
        "ordner",
        "datei",
        "onde",
        "ficheiro",
    ),
    "modify": (
        "modify",
        "edit",
        "change",
        "update",
        "patch",
        "extend",
        "adapt",
        "fix",
        "refactor",
        "touch",
        "modifica",
        "edita",
        "cambia",
        "actualiza",
        "parchea",
        "amplia",
        "adapta",
        "arregla",
        "retoca",
        "corrige",
        "modifie",
        "edite",
        "corrige",
        "andere",
        "bearbeite",
        "altera",
        "corrige",
    ),
    "existing_ref": (
        "existing",
        "same",
        "that",
        "this",
        "previous",
        "above",
        "current",
        "ese",
        "este",
        "mismo",
        "anterior",
        "previo",
        "actual",
        "lo",
        "eso",
        "aquel",
        "existant",
        "celui",
        "dies",
        "vorherige",
        "mesmo",
    ),
    "memory": (
        "remember",
        "recall",
        "memory",
        "know",
        "decision",
        "learning",
        "context",
        "recuerdas",
        "recuerda",
        "memoria",
        "sabes",
        "decision",
        "aprendizaje",
        "contexto",
        "souviens",
        "memoire",
        "weisst",
        "erinnerst",
        "lembras",
        "memoria",
    ),
    "identity": (
        "you",
        "your",
        "authorship",
        "author",
        "who",
        "session",
        "terminal",
        "client",
        "another",
        "tu",
        "tuyo",
        "autor",
        "autoria",
        "quien",
        "sesion",
        "terminal",
        "cliente",
        "otra",
        "otro",
        "toi",
        "auteur",
        "wer",
        "du",
        "sessao",
    ),
    "schedule": (
        "remind",
        "reminder",
        "followup",
        "schedule",
        "deadline",
        "due",
        "tomorrow",
        "later",
        "calendar",
        "commitment",
        "recuerda",
        "recordatorio",
        "followup",
        "seguimiento",
        "agenda",
        "plazo",
        "vence",
        "manana",
        "luego",
        "compromiso",
        "rappelle",
        "delai",
        "morgen",
        "frist",
        "lembra",
        "amanha",
    ),
    "runtime": (
        "nexo",
        "brain",
        "desktop",
        "mcp",
        "runtime",
        "server",
        "cli",
        "tool",
        "plugin",
        "startup",
        "diagnose",
        "broken",
        "install",
        "catalog",
        "router",
        "cerebro",
        "servidor",
        "herramienta",
        "plugin",
        "arranque",
        "diagnostica",
        "roto",
        "instalacion",
        "catalogo",
    ),
    "live_state": (
        "release",
        "published",
        "publish",
        "deployed",
        "deploy",
        "uploaded",
        "sent",
        "closed",
        "merged",
        "commit",
        "branch",
        "tag",
        "version",
        "server",
        "port",
        "dns",
        "domain",
        "ticket",
        "issue",
        "pr",
        "pull",
        "status",
        "running",
        "installed",
        "verified",
        "publicado",
        "publicada",
        "publicar",
        "desplegado",
        "desplegada",
        "subido",
        "subida",
        "enviado",
        "enviada",
        "cerrado",
        "cerrada",
        "mergeado",
        "rama",
        "servidor",
        "puerto",
        "dominio",
        "estado",
        "corriendo",
        "instalado",
        "verificado",
    ),
}

_INTENT_FEATURE_WEIGHTS: dict[str, dict[str, float]] = {
    "prior_work": {"past_work": 1.55, "existing_ref": 0.35, "memory": 0.25},
    "file_location": {"location": 1.65, "runtime": 0.20},
    "modify_existing": {"modify": 1.30, "existing_ref": 0.70, "location": 0.20, "past_work": 0.20},
    "memory_question": {"memory": 1.40, "past_work": 0.20, "identity": 0.15},
    "identity_authorship": {"identity": 1.20, "past_work": 0.65},
    "schedule_commitment": {"schedule": 1.55, "past_work": 0.10, "memory": 0.20},
    "live_state_claim": {"live_state": 1.45, "past_work": 0.45, "runtime": 0.25, "identity": 0.15},
    "runtime_diagnosis": {"runtime": 1.55, "location": 0.15},
}


@dataclass(frozen=True)
class IntentClassification:
    intent: str
    confidence: float
    scores: dict[str, float]
    features: dict[str, float]
    language_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceStep:
    name: str
    phase: str = "primary"
    timeout_ms: int = 300
    max_chars: int = 1200


@dataclass(frozen=True)
class SourcePlan:
    intent: str
    primary: tuple[SourceStep, ...] = ()
    fallback: tuple[SourceStep, ...] = ()

    def all_steps(self) -> tuple[SourceStep, ...]:
        return self.primary + self.fallback

    def source_names(self) -> list[str]:
        return [step.name for step in self.all_steps()]


@dataclass(frozen=True)
class SourceRequest:
    query: str
    intent: str
    sid: str = ""
    conversation_id: str = ""
    area: str = ""
    files: str = ""
    surface: str = "pre_answer"
    max_chars: int = 1200
    token_budget: int = DEFAULT_TOKEN_BUDGET
    current_context: str = ""
    budget_policy: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceResult:
    source: str
    ok: bool = True
    rendered: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    result_count: int = 0
    elapsed_ms: float = 0.0
    skipped: bool = False
    aborted_reason: str = ""
    error: str = ""
    phase: str = "primary"

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence_refs or self.rendered.strip() or self.result_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "ok": self.ok,
            "result_count": self.result_count,
            "elapsed_ms": round(float(self.elapsed_ms), 2),
            "skipped": self.skipped,
            "aborted_reason": self.aborted_reason,
            "error": self.error,
            "phase": self.phase,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass
class PreAnswerRoute:
    ok: bool
    intent: str
    confidence: float
    should_inject: bool
    rendered: str
    source_plan: SourcePlan
    sources: list[SourceResult]
    elapsed_ms: float
    evidence_refs: list[str] = field(default_factory=list)
    skipped_sources: list[str] = field(default_factory=list)
    aborted_reason: str = ""
    telemetry: dict[str, Any] = field(default_factory=dict)
    classification: IntentClassification | None = None
    budget_policy: dict[str, Any] = field(default_factory=dict)
    required_sources_count: int = 0
    missing_required_sources_count: int = 0
    optional_sources_skipped_count: int = 0
    required_source_timeouts: list[str] = field(default_factory=list)
    must_disclose_gap: bool = False
    gap_disclosed: bool = False
    decision_signal: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "intent": self.intent,
            "confidence": round(float(self.confidence), 4),
            "should_inject": self.should_inject,
            "rendered": self.rendered,
            "elapsed_ms": round(float(self.elapsed_ms), 2),
            "evidence_refs": list(self.evidence_refs),
            "skipped_sources": list(self.skipped_sources),
            "aborted_reason": self.aborted_reason,
            "source_plan": {
                "intent": self.source_plan.intent,
                "primary": [step.name for step in self.source_plan.primary],
                "fallback": [step.name for step in self.source_plan.fallback],
            },
            "sources": [source.to_dict() for source in self.sources],
            "telemetry": dict(self.telemetry),
            "budget_policy": dict(self.budget_policy),
            "budget_tier": self.budget_policy.get("budget_tier", ""),
            "budget_decision_uid": self.budget_policy.get("budget_decision_uid", ""),
            "policy_version": self.budget_policy.get("policy_version", ""),
            "first_response_deadline_ms": int(self.budget_policy.get("first_response_deadline_ms") or 0),
            "required_sources_count": int(self.required_sources_count),
            "missing_required_sources_count": int(self.missing_required_sources_count),
            "optional_sources_skipped_count": int(self.optional_sources_skipped_count),
            "required_source_timeouts": list(self.required_source_timeouts),
            "must_disclose_gap": bool(self.must_disclose_gap),
            "gap_disclosed": bool(self.gap_disclosed),
            "decision_signal": self.decision_signal,
            "classification": {
                "intent": self.classification.intent,
                "confidence": round(float(self.classification.confidence), 4),
                "scores": dict(self.classification.scores),
                "features": dict(self.classification.features),
                "language_hints": list(self.classification.language_hints),
            }
            if self.classification
            else None,
        }


SourceAdapter = Callable[[SourceRequest], SourceResult | dict[str, Any] | str | None]
TelemetrySink = Callable[[dict[str, Any]], None]


_SOURCE_PLANS: dict[str, SourcePlan] = {
    "prior_work": SourcePlan(
        intent="prior_work",
        primary=(
            SourceStep("semantic_layers", timeout_ms=120, max_chars=900),
            SourceStep("recent_context", timeout_ms=240),
            SourceStep("evidence_ledger", timeout_ms=260),
            SourceStep("commitments", timeout_ms=180),
            SourceStep("protocol_tasks", timeout_ms=240),
            SourceStep("workflows", timeout_ms=260),
            SourceStep("change_log", timeout_ms=260),
            SourceStep("causal_graph", timeout_ms=120, max_chars=900),
            SourceStep("kg_neighbors", timeout_ms=120, max_chars=900),
            SourceStep("associative_graph", timeout_ms=120, max_chars=900),
            SourceStep("diary", timeout_ms=260),
        ),
        fallback=(
            SourceStep("transcripts", phase="fallback", timeout_ms=700),
            SourceStep("memory", phase="fallback", timeout_ms=400),
            SourceStep("local_context", phase="fallback", timeout_ms=700, max_chars=700),
        ),
    ),
    "file_location": SourcePlan(
        intent="file_location",
        primary=(
            SourceStep("project_atlas", timeout_ms=140),
            SourceStep("local_context", timeout_ms=1200, max_chars=900),
        ),
        fallback=(
            SourceStep("filesystem", phase="fallback", timeout_ms=500),
            SourceStep("transcripts", phase="fallback", timeout_ms=600),
        ),
    ),
    "modify_existing": SourcePlan(
        intent="modify_existing",
        primary=(
            SourceStep("semantic_layers", timeout_ms=120, max_chars=900),
            SourceStep("project_atlas", timeout_ms=140),
            SourceStep("guard_context", timeout_ms=160),
            SourceStep("change_log", timeout_ms=300),
            SourceStep("workflows", timeout_ms=260),
            SourceStep("kg_neighbors", timeout_ms=120, max_chars=900),
            SourceStep("associative_graph", timeout_ms=120, max_chars=900),
        ),
        fallback=(
            SourceStep("transcripts", phase="fallback", timeout_ms=650),
            SourceStep("local_context", phase="fallback", timeout_ms=900, max_chars=900),
        ),
    ),
    "memory_question": SourcePlan(
        intent="memory_question",
        primary=(
            SourceStep("semantic_layers", timeout_ms=120, max_chars=900),
            SourceStep("commitments", timeout_ms=180),
            SourceStep("reminders", timeout_ms=260),
            SourceStep("followups", timeout_ms=260),
            SourceStep("diary", timeout_ms=280),
            SourceStep("evidence_ledger", timeout_ms=260),
            SourceStep("memory", timeout_ms=500),
            SourceStep("cognitive", timeout_ms=500),
        ),
        fallback=(
            SourceStep("transcripts", phase="fallback", timeout_ms=700),
            SourceStep("local_context", phase="fallback", timeout_ms=900, max_chars=900),
        ),
    ),
    "identity_authorship": SourcePlan(
        intent="identity_authorship",
        primary=(
            SourceStep("semantic_layers", timeout_ms=120, max_chars=900),
            SourceStep("recent_context", timeout_ms=240),
            SourceStep("evidence_ledger", timeout_ms=260),
            SourceStep("commitments", timeout_ms=180),
            SourceStep("diary", timeout_ms=280),
            SourceStep("change_log", timeout_ms=300),
            SourceStep("transcripts", timeout_ms=700),
            SourceStep("kg_neighbors", timeout_ms=120, max_chars=900),
        ),
        fallback=(SourceStep("continuity", phase="fallback", timeout_ms=400),),
    ),
    "schedule_commitment": SourcePlan(
        intent="schedule_commitment",
        primary=(
            SourceStep("semantic_layers", timeout_ms=120, max_chars=900),
            SourceStep("commitments", timeout_ms=180),
            SourceStep("reminders", timeout_ms=260),
            SourceStep("followups", timeout_ms=260),
            SourceStep("workflows", timeout_ms=280),
        ),
        fallback=(
            SourceStep("diary", phase="fallback", timeout_ms=260),
            SourceStep("transcripts", phase="fallback", timeout_ms=650),
        ),
    ),
    "live_state_claim": SourcePlan(
        intent="live_state_claim",
        primary=(
            SourceStep("semantic_layers", timeout_ms=120, max_chars=900),
            SourceStep("recent_context", timeout_ms=240),
            SourceStep("evidence_ledger", timeout_ms=260),
            SourceStep("change_log", timeout_ms=300),
            SourceStep("protocol_tasks", timeout_ms=240),
            SourceStep("workflows", timeout_ms=260),
            SourceStep("project_atlas", timeout_ms=160),
            SourceStep("system_catalog", timeout_ms=420),
            SourceStep("diary", timeout_ms=280),
            SourceStep("kg_neighbors", timeout_ms=120, max_chars=900),
        ),
        fallback=(
            SourceStep("transcripts", phase="fallback", timeout_ms=700),
            SourceStep("memory", phase="fallback", timeout_ms=500),
            SourceStep("local_context", phase="fallback", timeout_ms=900, max_chars=900),
        ),
    ),
    "runtime_diagnosis": SourcePlan(
        intent="runtime_diagnosis",
        primary=(
            SourceStep("system_catalog", timeout_ms=420),
            SourceStep("project_atlas", timeout_ms=160),
            SourceStep("runtime_docs", timeout_ms=300),
            SourceStep("kg_neighbors", timeout_ms=120, max_chars=900),
        ),
        fallback=(
            SourceStep("source_grep", phase="fallback", timeout_ms=600),
            SourceStep("runtime_db", phase="fallback", timeout_ms=400),
        ),
    ),
    "general": SourcePlan(intent="general"),
}

_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def _as_budget_list(policy: dict[str, Any], key: str) -> tuple[str, ...]:
    value = policy.get(key) if isinstance(policy, dict) else ()
    if isinstance(value, str):
        value = [item.strip() for item in value.split(",")]
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(str(item).strip() for item in value if str(item or "").strip())


def _budget_int(policy: dict[str, Any], key: str, default: int) -> int:
    try:
        value = int(policy.get(key))
    except Exception:
        return default
    return value if value >= 0 else default


def _step_allowed(step: SourceStep, *, allowed: set[str], forbidden: set[str], required: set[str]) -> bool:
    if step.name in forbidden:
        return False
    if not allowed:
        return True
    return step.name in allowed or step.name in required


def _budgeted_step(step: SourceStep, *, max_source_timeout_ms: int) -> SourceStep:
    timeout = step.timeout_ms
    if max_source_timeout_ms > 0:
        timeout = min(timeout, max_source_timeout_ms)
    return replace(step, timeout_ms=max(1, int(timeout)))


def _apply_budget_policy_to_plan(
    source_plan: SourcePlan,
    policy: dict[str, Any] | None,
    adapters: dict[str, SourceAdapter],
) -> SourcePlan:
    if not policy:
        return source_plan
    allowed = set(_as_budget_list(policy, "allowed_sources"))
    forbidden = set(_as_budget_list(policy, "forbidden_sources"))
    required = set(_as_budget_list(policy, "required_sources"))
    max_sources = _budget_int(policy, "max_sources", len(source_plan.all_steps()) or 999)
    max_source_timeout_ms = _budget_int(policy, "max_source_timeout_ms", 0)
    fallback_policy = str(policy.get("fallback_policy") or "")
    if max_sources <= 0:
        return SourcePlan(intent=source_plan.intent)

    selected: list[SourceStep] = []
    selected_names: set[str] = set()

    def _append(step: SourceStep) -> None:
        if len(selected) >= max_sources or step.name in selected_names:
            return
        if step.name not in adapters:
            return
        if not _step_allowed(step, allowed=allowed, forbidden=forbidden, required=required):
            return
        selected.append(_budgeted_step(step, max_source_timeout_ms=max_source_timeout_ms))
        selected_names.add(step.name)

    for required_name in required:
        if required_name in forbidden or required_name not in adapters:
            continue
        _append(SourceStep(required_name, timeout_ms=max_source_timeout_ms or 300))
    for step in source_plan.primary:
        _append(step)
    if fallback_policy != "primary_only":
        for step in source_plan.fallback:
            _append(step)

    primary: list[SourceStep] = []
    fallback: list[SourceStep] = []
    for step in selected:
        if step.phase == "fallback":
            fallback.append(step)
        else:
            primary.append(step)
    return SourcePlan(intent=source_plan.intent, primary=tuple(primary), fallback=tuple(fallback))


def classify_intent(query: str, *, current_context: str = "") -> IntentClassification:
    """Classify the turn through the semantic router first.

    The legacy concept-overlap scorer remains only as a degraded fallback for
    installs where the local semantic stack is unavailable.
    """
    text = f"{query or ''}\n{current_context or ''}".strip()
    semantic = _classify_intent_semantic(text)
    if semantic is not None:
        return semantic
    fallback = _classify_intent_fallback(text)
    conservative = _conservative_continuity_fallback(text, fallback)
    if conservative is not None:
        return conservative
    if _demote_cold_generic_continuity(text, fallback):
        return IntentClassification(
            intent="general",
            confidence=0.0,
            scores={**fallback.scores, "semantic_unavailable": 1.0},
            features={
                **fallback.features,
                "cold_generic_continuity_demoted": 1.0,
            },
            language_hints=fallback.language_hints,
        )
    return fallback


def _classify_intent_semantic(text: str) -> IntentClassification | None:
    if not text or not _pre_answer_semantic_intent_enabled():
        return None
    try:
        from semantic_router import route as semantic_route
    except Exception:
        return None

    result = semantic_route(
        decision_kind=PRE_ANSWER_SEMANTIC_DECISION_KIND,
        question=(
            "Classify the user's pre-answer need into exactly one label. "
            "prior_work means previous actions, reasons, evidence, or why an artifact was touched. "
            "file_location means where a file/project/artifact is. "
            "modify_existing means editing or continuing an existing artifact. "
            "memory_question means asking remembered facts, decisions, or context. "
            "identity_authorship means who did something, which session/client acted, or Nero authorship. "
            "schedule_commitment means promises, pending commitments, reminders, deadlines, or future follow-up. "
            "live_state_claim means current or past external state that needs evidence: releases, commits, branches, tags, tickets, servers, ports, DNS, deployments, sent messages, uploads, installs, or verified/closed status. "
            "runtime_diagnosis means diagnosing NEXO/Brain/Desktop/runtime/tools. "
            "general means no pre-answer continuity evidence is needed."
        ),
        context=text,
        labels=PRE_ANSWER_INTENTS,
        allow_remote_fallback=_pre_answer_semantic_remote_enabled(),
    )
    if not getattr(result, "ok", False):
        return None

    label = str(getattr(result, "label", None) or getattr(result, "verdict", "") or "").strip()
    if label not in PRE_ANSWER_INTENTS:
        return None

    confidence = _coerce_confidence(getattr(result, "confidence", 0.0), default=0.75)
    normalized = _normalize(text)
    tokens = _plain_tokens(normalized)
    return IntentClassification(
        intent=label,
        confidence=confidence if label != "general" else min(confidence, 0.2),
        scores={label: round(confidence, 4)},
        features={
            "semantic_route": 1.0,
            "semantic_confidence": round(confidence, 4),
        },
        language_hints=_language_hints(tokens),
    )


def _classify_intent_fallback(text: str) -> IntentClassification:
    normalized = _normalize(text)
    tokens = _plain_tokens(normalized)
    features = {name: _feature_score(tokens, terms) for name, terms in _FEATURE_LEXICON.items()}
    if _PATHISH_RE.search(normalized):
        features["location"] = min(2.0, features.get("location", 0.0) + 0.85)
    if _DATEISH_RE.search(normalized):
        features["schedule"] = min(2.0, features.get("schedule", 0.0) + 0.55)
    if "?" in text or normalized.startswith(("que ", "what ", "where ", "donde ", "quien ", "who ")):
        features["question_shape"] = 1.0

    scores: dict[str, float] = {}
    for intent, weights in _INTENT_FEATURE_WEIGHTS.items():
        raw = sum(features.get(feature, 0.0) * weight for feature, weight in weights.items())
        if intent == "modify_existing" and features.get("modify", 0.0) and features.get("existing_ref", 0.0):
            raw += 0.55
        if intent == "identity_authorship" and features.get("identity", 0.0) and features.get("past_work", 0.0):
            raw += 0.50
        if intent == "file_location" and features.get("location", 0.0) and _PATHISH_RE.search(normalized):
            raw += 0.35
        if intent == "runtime_diagnosis" and features.get("runtime", 0.0) and features.get("location", 0.0) > 1.2:
            raw -= 0.25
        scores[intent] = round(raw, 4)

    best_intent = max(scores, key=scores.get) if scores else "general"
    best_score = scores.get(best_intent, 0.0)
    confidence = _score_to_confidence(best_score)
    if best_score < 0.72:
        best_intent = "general"
        confidence = 0.0

    return IntentClassification(
        intent=best_intent,
        confidence=confidence,
        scores=scores,
        features={name: round(score, 4) for name, score in features.items() if score > 0},
        language_hints=_language_hints(tokens),
    )


def _conservative_continuity_fallback(
    text: str,
    fallback: IntentClassification,
) -> IntentClassification | None:
    """Route short operator continuity questions to safe evidence sources.

    This fallback is only a cold-start safety net. The semantic router remains
    the primary detector; here we require an operational anchor so generic
    questions do not inject unrelated memory or open commitments.
    """
    normalized = _normalize(text)
    tokens = _plain_tokens(normalized)
    if not tokens:
        return None
    question_like = "?" in text or normalized.startswith(("que ", "qué ", "what ", "which ", "who ", "quien ", "donde ", "where ", "why ", "por que ", "por qué "))
    if not question_like or len(tokens) > 9:
        return None
    pathish = bool(_PATHISH_RE.search(normalized))
    if (
        fallback.intent == "file_location"
        and pathish
        and fallback.features.get("location", 0.0) <= 0.95
    ):
        return IntentClassification(
            intent="prior_work",
            confidence=0.40,
            scores={"prior_work": 0.40, "semantic_unavailable": 1.0},
            features={"conservative_continuity_fallback": 1.0, "path_context": 1.0},
            language_hints=_language_hints(tokens),
        )
    if fallback.intent in {"file_location", "modify_existing", "runtime_diagnosis"} and fallback.confidence >= 0.60:
        return None
    if pathish and fallback.intent == "file_location":
        return None
    if not _cold_continuity_anchor(tokens):
        return None
    return IntentClassification(
        intent="memory_question",
        confidence=0.35,
        scores={"memory_question": 0.35, "semantic_unavailable": 1.0},
        features={"conservative_continuity_fallback": 1.0},
        language_hints=_language_hints(tokens),
    )


def _cold_continuity_anchor(tokens: Iterable[str]) -> bool:
    token_list = list(tokens)
    if not token_list:
        return False
    if any(token.startswith(stem) for token in token_list for stem in _COLD_CONTINUITY_STEMS):
        return True
    if _COLD_OPERATOR_TOKENS.intersection(token_list):
        return _feature_score(token_list, _FEATURE_LEXICON["past_work"]) >= 0.60
    return False


def _demote_cold_generic_continuity(text: str, fallback: IntentClassification) -> bool:
    if fallback.intent not in {"prior_work", "memory_question"}:
        return False
    normalized = _normalize(text)
    tokens = _plain_tokens(normalized)
    if not tokens or len(tokens) > 9:
        return False
    question_like = "?" in text or normalized.startswith(("que ", "qué ", "what ", "which ", "who ", "quien ", "donde ", "where ", "why ", "por que ", "por qué "))
    if not question_like:
        return False
    if _PATHISH_RE.search(normalized):
        return False
    return not _cold_continuity_anchor(tokens)


def _cold_commitment_question(text: str) -> bool:
    tokens = _plain_tokens(_normalize(text))
    if not tokens:
        return False
    if any(token.startswith(stem) for token in tokens for stem in _COLD_COMMITMENT_STEMS):
        return True
    return _feature_score(tokens, _FEATURE_LEXICON["schedule"]) >= 0.60


def _pre_answer_semantic_intent_enabled() -> bool:
    value = os.environ.get("NEXO_PRE_ANSWER_SEMANTIC_INTENT", "1")
    return str(value or "").strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _pre_answer_semantic_remote_enabled() -> bool:
    value = os.environ.get("NEXO_PRE_ANSWER_SEMANTIC_REMOTE", "0")
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _coerce_confidence(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed


def plan_sources(intent: str) -> SourcePlan:
    return _SOURCE_PLANS.get(intent, _SOURCE_PLANS["general"])


def route_pre_answer(
    query: str,
    *,
    sid: str = "",
    conversation_id: str = "",
    intent: str = "auto",
    area: str = "",
    files: str = "",
    budget_ms: int = DEFAULT_BUDGET_MS,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    current_context: str = "",
    source_adapters: dict[str, SourceAdapter] | None = None,
    telemetry_sink: TelemetrySink | None = None,
    budget_policy: dict[str, Any] | None = None,
    classification_override: IntentClassification | None = None,
) -> PreAnswerRoute:
    """Route a user turn through pre-answer context sources.

    The function is fail-open: source errors/timeouts become skipped source
    entries, never exceptions that block the user-visible answer.
    """
    started = time.monotonic()
    classification = (
        classification_override
        if classification_override is not None
        else classify_intent(query, current_context=current_context)
        if intent == "auto"
        else _manual_intent(intent)
    )
    base_source_plan = plan_sources(classification.intent)
    budget = _DeadlineBudget(budget_ms)
    adapters = dict(default_source_adapters())
    adapters.update(source_adapters or {})
    source_plan = _apply_budget_policy_to_plan(base_source_plan, budget_policy, adapters)
    required_sources = set(_as_budget_list(budget_policy or {}, "required_sources"))

    sources: list[SourceResult] = []
    evidence_refs: list[str] = []
    skipped_sources: list[str] = []

    if classification.intent != "general":
        for phase, steps in (("primary", source_plan.primary), ("fallback", source_plan.fallback)):
            if phase == "fallback" and evidence_refs:
                break
            for step in steps:
                if not budget.has_time(min_remaining_ms=20):
                    skipped = SourceResult(
                        source=step.name,
                        ok=False,
                        skipped=True,
                        aborted_reason="deadline_exhausted",
                        phase=step.phase,
                    )
                    sources.append(skipped)
                    skipped_sources.append(step.name)
                    continue
                adapter = adapters.get(step.name)
                if adapter is None:
                    skipped = SourceResult(
                        source=step.name,
                        ok=False,
                        skipped=True,
                        aborted_reason="source_unavailable",
                        phase=step.phase,
                    )
                    sources.append(skipped)
                    skipped_sources.append(step.name)
                    continue
                request = SourceRequest(
                    query=query,
                    intent=classification.intent,
                    sid=sid,
                    conversation_id=conversation_id,
                    area=area,
                    files=files,
                    surface=str((budget_policy or {}).get("surface") or "pre_answer"),
                    max_chars=step.max_chars,
                    token_budget=token_budget,
                    current_context=current_context,
                    budget_policy=dict(budget_policy or {}),
                )
                result = _run_source_step(adapter, request, step, budget)
                sources.append(result)
                if result.skipped:
                    skipped_sources.append(step.name)
                if result.has_evidence:
                    evidence_refs.extend(result.evidence_refs or [f"{result.source}:inline"])

    elapsed_ms = (time.monotonic() - started) * 1000
    consulted_or_evident = {source.source for source in sources if not source.skipped or source.has_evidence}
    required_source_timeouts = [
        source.source
        for source in sources
        if source.source in required_sources
        and source.skipped
        and source.aborted_reason in {"timeout", "deadline_exhausted", "source_unavailable", "source_error"}
    ]
    missing_required = sorted(required_sources - consulted_or_evident)
    missing_required_count = len(set(missing_required) | set(required_source_timeouts))
    optional_sources_skipped_count = sum(1 for source in sources if source.skipped and source.source not in required_sources)
    has_any_evidence = any(source.has_evidence for source in sources)
    rendered = render_route(
        query=query,
        classification=classification,
        sources=sources,
        token_budget=token_budget,
    )
    max_rendered_chars = _budget_int(budget_policy or {}, "max_rendered_chars", 0)
    if max_rendered_chars > 0:
        rendered = _clip(rendered, max_rendered_chars)
    elif budget_policy and max_rendered_chars == 0:
        rendered = ""
    aborted_reason = "required_source_timeout" if required_source_timeouts else _route_aborted_reason(sources, budget)
    must_disclose_gap = bool((budget_policy or {}).get("must_disclose_gap") or missing_required_count)
    decision_signal = "defer" if missing_required_count and (budget_policy or {}).get("fallback_policy") == "mandatory_fail_closed" else ""
    if classification.intent in EVIDENCE_REQUIRED_INTENTS and (not has_any_evidence or missing_required_count):
        gap = render_evidence_gap(
            query=query,
            classification=classification,
            sources=sources,
            missing_required_count=missing_required_count,
            required_source_timeouts=required_source_timeouts,
        )
        rendered = f"{rendered}\n\n{gap}" if rendered else gap
        must_disclose_gap = True
        decision_signal = "defer"
    should_inject = classification.intent in INJECTING_INTENTS and bool(rendered)
    telemetry = _build_route_event(
        query=query,
        route_intent=classification.intent,
        confidence=classification.confidence,
        should_inject=should_inject,
        sources=sources,
        elapsed_ms=elapsed_ms,
        budget_ms=budget_ms,
        sid=sid,
        conversation_id=conversation_id,
        area=area,
        files=files,
        aborted_reason=aborted_reason,
        budget_policy=budget_policy or {},
        required_sources_count=len(required_sources),
        missing_required_sources_count=missing_required_count,
        optional_sources_skipped_count=optional_sources_skipped_count,
        gap_disclosed=False,
    )
    _emit_telemetry(telemetry, telemetry_sink)

    return PreAnswerRoute(
        ok=True,
        intent=classification.intent,
        confidence=classification.confidence,
        should_inject=should_inject,
        rendered=rendered if should_inject else "",
        source_plan=source_plan,
        sources=sources,
        elapsed_ms=elapsed_ms,
        evidence_refs=list(dict.fromkeys(evidence_refs)),
        skipped_sources=list(dict.fromkeys(skipped_sources)),
        aborted_reason=aborted_reason,
        telemetry=telemetry,
        classification=classification,
        budget_policy=dict(budget_policy or {}),
        required_sources_count=len(required_sources),
        missing_required_sources_count=missing_required_count,
        optional_sources_skipped_count=optional_sources_skipped_count,
        required_source_timeouts=list(dict.fromkeys(required_source_timeouts)),
        must_disclose_gap=must_disclose_gap,
        gap_disclosed=False,
        decision_signal=decision_signal,
    )


def render_route(
    *,
    query: str,
    classification: IntentClassification,
    sources: Iterable[SourceResult],
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> str:
    source_items = [source for source in sources if source.has_evidence and not source.skipped]
    if not source_items:
        return ""
    lines = [
        "PRE-ANSWER CONTEXT",
        f"Intent: {classification.intent} ({classification.confidence:.2f})",
        "Use this only as context; verify before claiming completion.",
    ]
    for source in source_items:
        snippet = redact_secrets(source.rendered).strip()
        if not snippet:
            snippet = f"{source.result_count} result(s)"
        lines.append("")
        lines.append(f"[{source.source}]")
        lines.append(_clip(snippet, source_result_char_budget(token_budget)))
    return _clip("\n".join(lines), max(400, int(token_budget or DEFAULT_TOKEN_BUDGET) * 4))


def render_evidence_gap(
    *,
    query: str,
    classification: IntentClassification,
    sources: Iterable[SourceResult],
    missing_required_count: int = 0,
    required_source_timeouts: Iterable[str] = (),
) -> str:
    checked = [source.source for source in sources if not source.skipped]
    skipped = [source.source for source in sources if source.skipped]
    timeout_names = list(dict.fromkeys(required_source_timeouts))
    lines = [
        "PRE-ANSWER VERIFICATION GAP",
        f"Intent: {classification.intent} ({classification.confidence:.2f})",
        "The user is asking about state that requires evidence before any claim.",
        "Do not affirm, deny, or present a release/server/ticket/commit/action status as fact from recollection.",
        "If no stronger source is available in this turn, answer that the state is not verified yet and continue checking.",
    ]
    if checked:
        lines.append(f"Sources checked without enough evidence: {', '.join(dict.fromkeys(checked))}.")
    if skipped:
        lines.append(f"Sources skipped or unavailable: {', '.join(dict.fromkeys(skipped))}.")
    if missing_required_count:
        lines.append(f"Missing required source count: {missing_required_count}.")
    if timeout_names:
        lines.append(f"Required source timeouts: {', '.join(timeout_names)}.")
    return _clip("\n".join(lines), 1800)


def default_source_adapters() -> dict[str, SourceAdapter]:
    return {
        "semantic_layers": _source_semantic_layers,
        "recent_context": _source_recent_context,
        "evidence_ledger": _source_evidence_ledger,
        "protocol_tasks": _source_protocol_tasks,
        "workflows": _source_workflows,
        "change_log": _source_change_log,
        "causal_graph": _source_causal_graph,
        "kg_neighbors": _source_kg_neighbors,
        "associative_graph": _source_associative_graph,
        "diary": _source_diary,
        "transcripts": _source_transcripts,
        "memory": _source_memory,
        "project_atlas": _source_project_atlas,
        "local_context": _source_local_context,
        "filesystem": _source_filesystem,
        "guard_context": _source_guard_context,
        "cognitive": _source_cognitive,
        "commitments": _source_commitments,
        "continuity": _source_continuity,
        "reminders": _source_reminders,
        "followups": _source_followups,
        "system_catalog": _source_system_catalog,
        "runtime_docs": _source_runtime_docs,
        "source_grep": _source_source_grep,
        "runtime_db": _source_runtime_db,
    }


def redact_secrets(value: Any, *, max_chars: int | None = None) -> str:
    text = str(value or "")
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return _clip(text, max_chars) if max_chars else text


class _DeadlineBudget:
    def __init__(self, budget_ms: int):
        self.budget_ms = max(1, int(budget_ms or DEFAULT_BUDGET_MS))
        self.started = time.monotonic()

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started) * 1000

    def remaining_ms(self) -> float:
        return max(0.0, self.budget_ms - self.elapsed_ms())

    def has_time(self, *, min_remaining_ms: int = 1) -> bool:
        return self.remaining_ms() >= min_remaining_ms


def _run_source_step(
    adapter: SourceAdapter,
    request: SourceRequest,
    step: SourceStep,
    budget: _DeadlineBudget,
) -> SourceResult:
    source_started = time.monotonic()
    timeout_ms = min(max(1, int(step.timeout_ms)), max(1, int(budget.remaining_ms())))
    future = _executor().submit(adapter, request)
    try:
        raw = future.result(timeout=timeout_ms / 1000)
        result = _coerce_source_result(raw, source=step.name, phase=step.phase)
    except concurrent.futures.TimeoutError:
        future.cancel()
        result = SourceResult(
            source=step.name,
            ok=False,
            skipped=True,
            aborted_reason="timeout",
            phase=step.phase,
        )
    except Exception as exc:  # noqa: BLE001 - fail open by design
        result = SourceResult(
            source=step.name,
            ok=False,
            skipped=True,
            aborted_reason="source_error",
            error=redact_secrets(str(exc), max_chars=240),
            phase=step.phase,
        )
    result.elapsed_ms = (time.monotonic() - source_started) * 1000
    result.phase = step.phase
    return result


def _coerce_source_result(raw: SourceResult | dict[str, Any] | str | None, *, source: str, phase: str) -> SourceResult:
    if raw is None:
        return SourceResult(source=source, ok=True, phase=phase)
    if isinstance(raw, SourceResult):
        raw.source = raw.source or source
        raw.phase = phase
        raw.rendered = redact_secrets(raw.rendered)
        return raw
    if isinstance(raw, str):
        clean = redact_secrets(raw)
        return SourceResult(
            source=source,
            ok=True,
            rendered=clean,
            evidence_refs=[f"{source}:text"] if clean.strip() else [],
            result_count=1 if clean.strip() else 0,
            phase=phase,
        )
    rendered = str(raw.get("rendered") or raw.get("text") or raw.get("summary") or "")
    evidence_refs = raw.get("evidence_refs") or raw.get("refs") or []
    if isinstance(evidence_refs, str):
        evidence_refs = [evidence_refs]
    result_count = raw.get("result_count")
    if result_count is None:
        payload_results = raw.get("results") or raw.get("items") or []
        result_count = len(payload_results) if isinstance(payload_results, list) else (1 if rendered else 0)
    return SourceResult(
        source=str(raw.get("source") or source),
        ok=bool(raw.get("ok", True)),
        rendered=redact_secrets(rendered),
        evidence_refs=[str(ref) for ref in evidence_refs],
        result_count=int(result_count or 0),
        skipped=bool(raw.get("skipped", False)),
        aborted_reason=str(raw.get("aborted_reason") or ""),
        error=redact_secrets(raw.get("error") or "", max_chars=240),
        phase=phase,
    )


def _executor() -> concurrent.futures.ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(2, MAX_SOURCE_WORKERS),
            thread_name_prefix="nexo-pre-answer",
        )
    return _EXECUTOR


def shutdown_executor(*, wait: bool = True) -> None:
    """Stop source workers before shared runtime resources are closed."""
    global _EXECUTOR
    executor = _EXECUTOR
    _EXECUTOR = None
    if executor is not None:
        executor.shutdown(wait=wait, cancel_futures=True)


def _manual_intent(intent: str) -> IntentClassification:
    clean = (intent or "").strip()
    if clean not in PRE_ANSWER_INTENTS:
        clean = "general"
    return IntentClassification(intent=clean, confidence=1.0 if clean != "general" else 0.0, scores={clean: 1.0}, features={})


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return normalized.lower()


def _plain_tokens(normalized: str) -> list[str]:
    raw = _WORD_RE.findall(normalized)
    tokens: list[str] = []
    for item in raw:
        tokens.extend(_PLAIN_WORD_RE.findall(item.replace("/", " ").replace(".", " ").replace("-", " ")))
    return [token for token in tokens if token]


def _feature_score(tokens: list[str], terms: tuple[str, ...]) -> float:
    if not tokens:
        return 0.0
    normalized_terms = tuple(_normalize(term) for term in terms)
    total = 0.0
    matched_terms: set[str] = set()
    for token in tokens:
        best_term = ""
        best = 0.0
        for term in normalized_terms:
            score = _token_similarity(token, term)
            if score > best:
                best = score
                best_term = term
        if best >= 0.58 and best_term not in matched_terms:
            total += best
            matched_terms.add(best_term)
    return min(2.0, total)


def _token_similarity(token: str, term: str) -> float:
    if token == term:
        return 1.0
    if len(token) >= 4 and len(term) >= 4 and (token.startswith(term[:5]) or term.startswith(token[:5])):
        return 0.78
    if len(token) < 4 or len(term) < 4:
        return 0.0
    overlap = _ngram_jaccard(token, term)
    return 0.66 if overlap >= 0.58 else 0.0


def _ngram_jaccard(left: str, right: str, *, n: int = 3) -> float:
    def grams(value: str) -> set[str]:
        if len(value) <= n:
            return {value}
        return {value[idx : idx + n] for idx in range(0, len(value) - n + 1)}

    a = grams(left)
    b = grams(right)
    return len(a & b) / max(1, len(a | b))


def _score_to_confidence(score: float) -> float:
    return max(0.0, min(0.99, score / 3.2))


def _language_hints(tokens: list[str]) -> tuple[str, ...]:
    joined = set(tokens)
    hints: list[str] = []
    if joined & {"que", "donde", "quien", "recuerdas", "archivo", "manana", "hiciste"}:
        hints.append("es")
    if joined & {"what", "where", "who", "remember", "file", "tomorrow", "done"}:
        hints.append("en")
    if joined & {"ou", "quoi", "souviens", "fichier"}:
        hints.append("fr")
    if joined & {"wer", "wo", "datei", "morgen"}:
        hints.append("de")
    if joined & {"onde", "lembras", "ficheiro", "amanha"}:
        hints.append("pt")
    return tuple(hints)


def source_result_char_budget(token_budget: int) -> int:
    return max(280, min(1400, int(token_budget or DEFAULT_TOKEN_BUDGET) * 2))


def _clip(text: str, max_chars: int | None) -> str:
    if not max_chars or len(text) <= max_chars:
        return text
    return text[: max(0, int(max_chars) - 1)].rstrip() + "..."


def _route_aborted_reason(sources: list[SourceResult], budget: _DeadlineBudget) -> str:
    if any(source.aborted_reason == "timeout" for source in sources):
        return "source_timeout"
    if any(source.aborted_reason == "deadline_exhausted" for source in sources) or not budget.has_time():
        return "deadline_exhausted"
    return ""


def _build_route_event(
    *,
    query: str,
    route_intent: str,
    confidence: float,
    should_inject: bool,
    sources: list[SourceResult],
    elapsed_ms: float,
    budget_ms: int,
    sid: str,
    conversation_id: str,
    area: str,
    files: str,
    aborted_reason: str,
    budget_policy: dict[str, Any] | None = None,
    required_sources_count: int = 0,
    missing_required_sources_count: int = 0,
    optional_sources_skipped_count: int = 0,
    gap_disclosed: bool = False,
) -> dict[str, Any]:
    consulted = [source.source for source in sources if not source.skipped]
    skipped = [source.source for source in sources if source.skipped]
    policy = budget_policy or {}
    return {
        "event": "pre_answer_route",
        "query_hash": hashlib.sha256(str(query or "").encode("utf-8", errors="ignore")).hexdigest(),
        "intent": route_intent,
        "confidence": round(float(confidence), 4),
        "should_inject": bool(should_inject),
        "elapsed_ms": round(float(elapsed_ms), 2),
        "budget_ms": int(budget_ms or DEFAULT_BUDGET_MS),
        "budget_tier": str(policy.get("budget_tier") or ""),
        "budget_decision_uid": str(policy.get("budget_decision_uid") or ""),
        "policy_version": str(policy.get("policy_version") or ""),
        "surface": str(policy.get("surface") or ""),
        "risk_level": str(policy.get("risk_level") or ""),
        "first_response_deadline_ms": int(policy.get("first_response_deadline_ms") or 0),
        "required_sources_count": int(required_sources_count),
        "missing_required_sources_count": int(missing_required_sources_count),
        "optional_sources_skipped_count": int(optional_sources_skipped_count),
        "gap_disclosed": bool(gap_disclosed),
        "sources_consulted": consulted,
        "sources_skipped": skipped,
        "source_stats": [
            {
                "source": source.source,
                "phase": source.phase,
                "ok": source.ok,
                "elapsed_ms": round(float(source.elapsed_ms), 2),
                "result_count": int(source.result_count or 0),
                "evidence_refs_count": len(source.evidence_refs or []),
                "aborted_reason": source.aborted_reason,
            }
            for source in sources
        ],
        "evidence_refs_count": sum(len(source.evidence_refs or []) for source in sources),
        "aborted_reason": aborted_reason,
        "sid": redact_secrets(sid, max_chars=120),
        "conversation_id": redact_secrets(conversation_id, max_chars=120),
        "area": redact_secrets(area, max_chars=120),
        "files_hash": hashlib.sha256(str(files or "").encode("utf-8", errors="ignore")).hexdigest() if files else "",
    }


def _emit_telemetry(event: dict[str, Any], sink: TelemetrySink | None) -> None:
    if sink is not None:
        try:
            sink(event)
        except Exception:
            return
    _try_record_route_event(event)


def _try_record_route_event(event: dict[str, Any]) -> None:
    """Best-effort future schema support.

    G01 cannot add the table. If G15/G00 add ``pre_answer_route_events`` later
    with an ``event_json`` column, this module starts writing automatically.
    """
    try:
        import db

        conn = db.get_db()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pre_answer_route_events'"
        ).fetchone()
        if not row:
            return
        columns = {
            str(item["name"])
            for item in conn.execute("PRAGMA table_info(pre_answer_route_events)").fetchall()
            if "name" in item.keys()
        }
        if "event_json" not in columns:
            return
        conn.execute(
            "INSERT INTO pre_answer_route_events(event_json, created_at) VALUES (?, datetime('now'))",
            (json.dumps(event, ensure_ascii=False, sort_keys=True),),
        )
        conn.commit()
    except Exception:
        return


def _source_semantic_layers(request: SourceRequest) -> SourceResult:
    """Read fresh semantic layers only; never build or recompress in pre-answer."""
    try:
        from semantic_layers import select_semantic_layers
    except Exception as exc:
        return SourceResult(
            source="semantic_layers",
            ok=False,
            skipped=True,
            aborted_reason="source_error",
            error=str(exc),
        )

    scope_hint: dict[str, str] = {}
    if request.sid:
        scope_hint = {"scope_type": "session", "scope_id": request.sid}
    elif request.conversation_id:
        scope_hint = {"scope_type": "conversation", "scope_id": request.conversation_id}
    if not scope_hint:
        return SourceResult(source="semantic_layers")

    result = select_semantic_layers(
        query=request.query,
        intent_bundle={"intent_kind": request.intent},
        budget_policy=request.budget_policy,
        surface=request.surface,
        scope_hint=scope_hint,
    )
    layers = result.get("layers") or []
    rendered = str(result.get("rendered") or "")
    if not layers or not rendered.strip():
        return SourceResult(source="semantic_layers")

    refs: list[str] = []
    for layer in layers:
        for ref in (layer.get("source_refs") or []) + (layer.get("evidence_refs") or []):
            clean = str(ref or "").strip()
            if clean and clean not in refs:
                refs.append(clean)
    return SourceResult(
        source="semantic_layers",
        rendered=_clip(rendered, request.max_chars),
        evidence_refs=refs,
        result_count=len(layers),
    )


def _source_recent_context(request: SourceRequest) -> SourceResult:
    from db import build_pre_action_context, format_pre_action_context_bundle

    bundle = build_pre_action_context(query=request.query, session_id=request.sid, hours=24, limit=5)
    rendered = format_pre_action_context_bundle(bundle, compact=True)
    if not bundle.get("has_matches"):
        return SourceResult(source="recent_context")
    return SourceResult(
        source="recent_context",
        rendered=_clip(rendered, request.max_chars),
        evidence_refs=[f"hot_context:{item.get('context_key')}" for item in bundle.get("contexts") or [] if item.get("context_key")],
        result_count=len(bundle.get("contexts") or []) + len(bundle.get("events") or []),
    )


def _source_evidence_ledger(request: SourceRequest) -> SourceResult:
    from evidence_ledger import evidence_to_dicts, search_evidence

    entries = evidence_to_dicts(
        search_evidence(
            request.query,
            artifact=request.files,
            conversation_id=request.conversation_id,
            limit=4,
        )
    )
    return _rows_result(
        "evidence_ledger",
        entries,
        ("evidence_id", "source_type", "object_ref", "action", "summary", "created_at"),
        request.max_chars,
    )


def _source_protocol_tasks(request: SourceRequest) -> SourceResult:
    rows = _query_table_like(
        "protocol_tasks",
        request.query,
        columns=("task_id", "goal", "description", "context_hint", "files", "status"),
        limit=4,
    )
    return _rows_result("protocol_tasks", rows, ("task_id", "goal", "description", "status"), request.max_chars)


def _source_workflows(request: SourceRequest) -> SourceResult:
    from db import list_workflow_runs

    rows = list_workflow_runs(include_closed=False, limit=8)
    rows = _filter_rows_by_query(rows, request.query, ("run_id", "goal", "next_action", "status", "workflow_kind"))[:4]
    return _rows_result("workflows", rows, ("run_id", "goal", "next_action", "status"), request.max_chars)


def _source_change_log(request: SourceRequest) -> SourceResult:
    from db import search_changes

    rows = search_changes(query=request.query, files=request.files, days=45)[:4]
    return _rows_result("change_log", rows, ("id", "files", "what_changed", "why", "created_at"), request.max_chars)


def _source_causal_graph(request: SourceRequest) -> SourceResult:
    try:
        import causal_graph
    except Exception as exc:
        return SourceResult(source="causal_graph", ok=False, skipped=True, aborted_reason="source_error", error=str(exc))

    refs: list[tuple[str, str]] = []
    for raw in (request.files or "").split(","):
        clean = raw.strip()
        if clean:
            refs.append(("file", clean))
    if not refs:
        for match in _PATHISH_RE.findall(request.query or ""):
            refs.append(("file", match))
        for match in re.findall(r"\b[\w.-]+(?:/[\w.@+-]+)+\b", request.query or ""):
            refs.append(("file", match))
    if not refs:
        return SourceResult(source="causal_graph")

    rendered_parts: list[str] = []
    evidence_refs: list[str] = []
    result_count = 0
    for ref_type, ref in refs[:3]:
        result = causal_graph.query_edges(
            ref_type=ref_type,
            ref=ref,
            project_key=request.area,
            include_historical=False,
            limit=4,
        )
        if not result.get("has_evidence"):
            continue
        result_count += len(result.get("edges") or [])
        rendered_parts.append(causal_graph.render_query_result(result, max_chars=request.max_chars))
        for edge in result.get("edges") or []:
            props = edge.get("properties_dict") or {}
            evidence_refs.extend(str(item) for item in props.get("evidence_refs") or [] if str(item).strip())
    if not rendered_parts:
        return SourceResult(source="causal_graph")
    return SourceResult(
        source="causal_graph",
        rendered="\n".join(rendered_parts),
        evidence_refs=list(dict.fromkeys(evidence_refs)),
        result_count=result_count,
    )


def _source_kg_neighbors(request: SourceRequest) -> SourceResult:
    """KG neighbors + verified causal/ops edges for entities/files in the query.

    task_close (7.32.0) writes causal/provenance edges but nothing READ the KG at
    answer time, so the richer non-causal structure (touched/applies_to/belongs_to/
    mentions/...) never reached an answer. This bounded, fail-open, 1-hop source
    reads it. Hard-limited (<=3 refs, <=6 neighbors), index-backed, respects the
    per-source timeout — it can never block the answer.
    """
    try:
        import knowledge_graph as kg
        import causal_graph
    except Exception as exc:
        return SourceResult(source="kg_neighbors", ok=False, skipped=True, aborted_reason="source_error", error=str(exc))

    refs: list[str] = []
    for raw in (request.files or "").split(","):
        clean = raw.strip()
        if clean:
            refs.append(clean)
    if not refs:
        for match in _PATHISH_RE.findall(request.query or ""):
            refs.append(match)
        for match in re.findall(r"\b[\w.-]+(?:/[\w.@+-]+)+\b", request.query or ""):
            refs.append(match)
    refs = list(dict.fromkeys(refs))
    if not refs:
        return SourceResult(source="kg_neighbors")

    rendered_parts: list[str] = []
    evidence_refs: list[str] = []
    result_count = 0
    for ref in refs[:3]:
        try:
            node = None
            for ntype, nref in (("file", ref), ("file", f"file:{ref}"), ("entity", ref), ("entity", f"entity:{ref}")):
                node = kg.get_node(ntype, nref)
                if node:
                    break
            if node:
                for nb in kg.get_neighbors(int(node["id"]), active_only=True)[:6]:
                    relation = str(nb.get("relation") or "")
                    if relation.startswith("causal:") or relation.startswith("ops:"):
                        continue  # surfaced via query_edges below (avoid duplicate)
                    line = f"- {relation} ({nb.get('direction')}) {nb.get('node_type')}:{nb.get('node_ref')}"
                    if nb.get("label"):
                        line += f" ({nb.get('label')})"
                    rendered_parts.append(line)
                    evidence_refs.append(f"kg:node:{node['id']}:{nb.get('id')}")
                    result_count += 1
            cg = causal_graph.query_edges(
                ref_type="file", ref=ref, project_key=request.area, include_historical=False, limit=4,
            )
            if cg.get("has_evidence"):
                rendered_parts.append(causal_graph.render_query_result(cg, max_chars=request.max_chars))
                result_count += len(cg.get("edges") or [])
                for edge in cg.get("edges") or []:
                    props = edge.get("properties_dict") or {}
                    evidence_refs.extend(str(i) for i in props.get("evidence_refs") or [] if str(i).strip())
        except Exception:
            continue
    if not rendered_parts:
        return SourceResult(source="kg_neighbors")
    return SourceResult(
        source="kg_neighbors",
        rendered=_clip("\n".join(rendered_parts), request.max_chars),
        evidence_refs=list(dict.fromkeys(evidence_refs)),
        result_count=result_count,
    )


def _associative_graph_basename_match(kg, ref: str, *, limit: int = 3) -> list[int]:
    """Resolve a bare basename to KG file node ids via one bounded indexed LIKE.

    Only fires for path-ish refs (contains a '.' extension or '/'), so a generic
    word never triggers a table-wide LIKE. Returns at most ``limit`` node ids.
    """
    clean = str(ref or "").strip()
    if not clean or ("." not in clean and "/" not in clean):
        return []
    base = clean.rsplit("/", 1)[-1]
    if len(base) < 4:
        return []
    try:
        rows = kg._get_db().execute(
            "SELECT id FROM kg_nodes WHERE node_type='file' AND node_ref LIKE ? LIMIT ?",
            (f"%{base}", int(limit)),
        ).fetchall()
        return [int(r["id"]) for r in rows]
    except Exception:
        return []


def _associative_graph_seeds(request: SourceRequest, kg, *, max_seeds: int = 8) -> dict[int, float]:
    """Resolve the personalization vector for the associative-graph PPR.

    Two sources, union'd and capped (plan section 2.1):
      (i)  entities — entity_live_profile.resolve_entity(limit=8) — only when the
           query looks entity/path-worthy (reuses the local_context gate so we do
           NOT scan the entities table on generic queries).
      (ii) paths/files — request.files or the _PATHISH_RE / slash-token regex,
           same extraction as kg_neighbors.

    Returns {kg_node_id: weight}. Weight = entity score (i) or 1.0 (ii).
    """
    seeds: dict[int, float] = {}

    # (i) Entities — gated by the same worthiness check used for local_context so
    # generic queries never pay the full-table scan in resolve_entity.
    if _local_context_query_worthwhile(request):
        try:
            import entity_live_profile

            resolved = entity_live_profile.resolve_entity(request.query or "", limit=max_seeds)
            for cand in (resolved.get("candidates") or [])[:max_seeds]:
                ent_id = cand.get("entity_id")
                if not ent_id:
                    continue
                node = kg.get_node("entity", f"entity:{ent_id}") or kg.get_node("entity", str(ent_id))
                if node:
                    nid = int(node["id"])
                    score = float(cand.get("score") or 0.0) or 1.0
                    seeds[nid] = max(seeds.get(nid, 0.0), score)
        except Exception:
            pass

    # (ii) Paths / files — identical extraction to kg_neighbors.
    refs: list[str] = []
    for raw in (request.files or "").split(","):
        clean = raw.strip()
        if clean:
            refs.append(clean)
    if not refs:
        for match in _PATHISH_RE.findall(request.query or ""):
            refs.append(match)
        for match in re.findall(r"\b[\w.-]+(?:/[\w.@+-]+)+\b", request.query or ""):
            refs.append(match)
    for ref in list(dict.fromkeys(refs)):
        try:
            node = None
            for ntype, nref in (("file", ref), ("file", f"file:{ref}"), ("entity", ref), ("entity", f"entity:{ref}")):
                node = kg.get_node(ntype, nref)
                if node:
                    break
            if node:
                seeds.setdefault(int(node["id"]), 1.0)
            else:
                # Basename suffix-match: the KG stores files under full paths
                # (file:/Users/.../foo.py) while a query usually carries the bare
                # basename (foo.py). One bounded indexed LIKE recovers those — a
                # capability kg_neighbors (exact-match only) lacks.
                for nid in _associative_graph_basename_match(kg, ref, limit=3):
                    seeds.setdefault(nid, 1.0)
                    if len(seeds) >= max_seeds:
                        break
        except Exception:
            continue
        if len(seeds) >= max_seeds:
            break

    # Cap to <=max_seeds (entities first by insertion order, then paths).
    if len(seeds) > max_seeds:
        seeds = dict(list(seeds.items())[:max_seeds])
    return seeds


def _source_associative_graph(request: SourceRequest) -> SourceResult:
    """Multi-hop associative recall via Personalized PageRank over the KG (Ola 2).

    Generalises ``kg_neighbors`` (bounded 1-hop fan-out) to a ranked multi-hop
    spreading-activation (HippoRAG2-style "connect the dots at answer time").
    Seeds from query entities + paths, runs a pure-Python forward-push PPR over
    the active ``kg_edges`` (column-stochastic -> hub-safe), and surfaces the
    top-ranked related nodes that a 1-hop fan-out would miss.

    Fail-open absolute: any error / missing module / 0 seeds returns a bare
    SourceResult (no evidence). Bounded by ``max_push`` and the per-source
    timeout — it can never block the answer. Refs are ``kg:node:<id>`` (cacheable
    via the resolution_cache global watermark, identical to kg_neighbors).
    """
    try:
        import knowledge_graph as kg
        import ppr
    except Exception as exc:
        return SourceResult(
            source="associative_graph", ok=False, skipped=True,
            aborted_reason="source_error", error=str(exc),
        )

    try:
        seeds = _associative_graph_seeds(request, kg, max_seeds=ppr.DEFAULT_MAX_SEEDS)
        if not seeds:
            return SourceResult(source="associative_graph")

        # Cold-start contract: the FULL graph build (~13k edges) plus a cold
        # process's imports/first-DB-touch overruns the 120ms step timeout, which
        # would make the dispatcher abort the step and the feature contribute
        # nothing on query-1. So we never build inline. If the per-process cache
        # is already warm (query-2+, or a process whose pre-warm finished), we run
        # the multi-hop PPR straight off the cached graph (~5-7ms). If it is cold,
        # we kick off a non-blocking background pre-warm and degrade THIS query to
        # the bounded 1-hop fan-out (parity with kg_neighbors) — fast, never times
        # out — so the next query gets multi-hop.
        if ppr.cache_is_warm():
            ranked = ppr.rank_related(seeds, top_n=ppr.DEFAULT_TOP_N)
            if not ranked:
                # Warm but PPR returned nothing (e.g. seeds isolated) -> 1-hop.
                ranked = ppr.fallback_neighbors(list(seeds), limit=6)
        else:
            ppr.prewarm_async()
            ranked = ppr.fallback_neighbors(list(seeds), limit=6)
        if not ranked:
            return SourceResult(source="associative_graph")

        rendered_parts: list[str] = []
        evidence_refs: list[str] = []
        for node in ranked:
            line = f"- {node.node_type}:{node.node_ref}"
            if node.label:
                line += f" ({node.label})"
            if node.score:
                line += f" [ppr={node.score:.4f}]"
            rendered_parts.append(line)
            evidence_refs.append(f"kg:node:{node.node_id}")

        return SourceResult(
            source="associative_graph",
            rendered=_clip("\n".join(rendered_parts), request.max_chars),
            evidence_refs=list(dict.fromkeys(evidence_refs)),
            result_count=len(ranked),
        )
    except Exception as exc:
        return SourceResult(
            source="associative_graph", ok=False, skipped=True,
            aborted_reason="source_error", error=str(exc),
        )


def _source_diary(request: SourceRequest) -> SourceResult:
    from db import read_session_diary

    rows = read_session_diary(last_day=True, last_n=6)[:8]
    rows = _filter_rows_by_query(rows, request.query, ("summary", "context", "pending", "mental_state", "session_id"))[:3]
    return _rows_result("diary", rows, ("session_id", "summary", "pending", "created_at"), request.max_chars)


def _source_transcripts(request: SourceRequest) -> SourceResult:
    try:
        import db
        from transcript_index import _row_ref_matches
        from transcript_utils import _score_text_match, _tokenize
        from transcript_utils import MAX_TRANSCRIPT_HOURS

        conn = db.get_db()
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transcript_index'"
        ).fetchone()
        if table is None:
            return SourceResult(source="transcripts", skipped=True, aborted_reason="source_unavailable")

        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM transcript_index ORDER BY modified_at DESC LIMIT 5000"
            ).fetchall()
        ]
        cutoff = datetime.now() - timedelta(hours=max(1, int(MAX_TRANSCRIPT_HOURS or 72)))
        query_tokens = _tokenize(request.query)
        indexed_rows: list[dict[str, Any]] = []
        for row in rows:
            haystack = " ".join(
                str(row.get(field) or "")
                for field in ("sanitized_summary", "display_name", "session_id", "conversation_id", "path_ref", "metadata_json")
            )
            score = _score_text_match(query_tokens, haystack) if query_tokens else 0.0
            ref_matches = _row_ref_matches(request.query, row)
            if ref_matches:
                score = max(score, 2.0)
            modified = str(row.get("modified_at") or "")
            stale = False
            if modified:
                try:
                    stale = datetime.fromisoformat(modified) < cutoff
                except Exception:
                    pass
            if stale and not ref_matches and score < STRONG_TRANSCRIPT_INDEX_MATCH:
                continue
            if score <= 0:
                continue
            row["_score"] = round(score, 4)
            indexed_rows.append(row)
        indexed_rows.sort(key=lambda row: (float(row.get("_score") or 0), str(row.get("modified_at") or "")), reverse=True)
        indexed_rows = indexed_rows[:4]
        if indexed_rows:
            indexed_result = _rows_result(
                "transcript_index",
                indexed_rows,
                ("source_client", "display_name", "session_id", "sanitized_summary", "modified_at"),
                request.max_chars,
            )
            return SourceResult(
                source="transcripts",
                rendered=indexed_result.rendered,
                evidence_refs=indexed_result.evidence_refs,
                result_count=indexed_result.result_count,
            )
    except Exception as exc:
        return SourceResult(source="transcripts", ok=False, skipped=True, aborted_reason="source_error", error=str(exc))
    return SourceResult(source="transcripts")


def _source_memory(request: SourceRequest) -> SourceResult:
    from db import recall

    rows = recall(request.query, days=45)[:5]
    # ``recall`` returns heterogeneous FTS rows keyed by (source, source_id) in
    # the unified_search index, NOT a single ``id`` column. The default
    # ``_rows_result`` ref would collapse to a POSITIONAL ``memory:<idx>`` that
    # identifies no row, so the resolution cache could not version it (and would
    # refuse to cache, or worse, serve stale). Emit a RESOLVABLE ref
    # ``memory:<source>:<source_id>`` that the cache versions via
    # unified_search(source, source_id).updated_at, so an edited memory row
    # invalidates the cached answer. Falls back to the positional ref only when
    # a row carries no (source, source_id) pair (which the cache then treats as
    # untrackable and refuses to cache — conservative, never stale).
    if not rows:
        return SourceResult(source="memory")
    result = _rows_result("memory", rows, ("source", "title", "snippet", "category"), request.max_chars)
    resolvable_refs: list[str] = []
    for row in rows[:5]:
        src = str(row.get("source") or "").strip()
        sid = str(row.get("source_id") or "").strip()
        if src and sid:
            resolvable_refs.append(f"memory:{src}:{sid}")
    if resolvable_refs:
        result.evidence_refs = resolvable_refs
    return result


def _source_project_atlas(request: SourceRequest) -> SourceResult:
    atlas_path = _project_atlas_path()
    if not atlas_path.is_file():
        return SourceResult(source="project_atlas")
    try:
        atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return SourceResult(source="project_atlas", ok=False, skipped=True, aborted_reason="source_error", error=str(exc))
    query_norm = _normalize(" ".join([request.query, request.area, request.files]))
    matches: list[dict[str, Any]] = []
    for name, value in atlas.items():
        if name.startswith("_") or not isinstance(value, dict):
            continue
        aliases = value.get("aliases") or []
        blob = _normalize(" ".join([name, value.get("description") or "", " ".join(map(str, aliases))]))
        if any(_token_similarity(token, term) >= 0.58 for token in _plain_tokens(query_norm) for term in _plain_tokens(blob)):
            matches.append({"name": name, **value})
    rows = matches[:3]
    rendered_lines = []
    for item in rows:
        locations = item.get("locations") or {}
        rendered_lines.append(f"- {item.get('name')}: {item.get('description') or ''}")
        for key, path in list(locations.items())[:4]:
            rendered_lines.append(f"  {key}: {path}")
    return SourceResult(
        source="project_atlas",
        rendered=_clip("\n".join(rendered_lines), request.max_chars),
        evidence_refs=[f"project_atlas:{item.get('name')}" for item in rows],
        result_count=len(rows),
    )


def _source_local_context(request: SourceRequest) -> SourceResult:
    from local_context import api as local_context_api

    mode = _local_context_pre_answer_mode()
    if mode == "off":
        _record_local_context_skip(request, mode=mode, reason="disabled")
        return SourceResult(
            source="local_context",
            ok=True,
            skipped=True,
            aborted_reason="disabled",
        )
    if not _local_context_query_worthwhile(request):
        _record_local_context_skip(request, mode=mode, reason="adaptive_skip")
        return SourceResult(
            source="local_context",
            ok=True,
            skipped=True,
            aborted_reason="adaptive_skip",
        )

    started = time.monotonic()
    payload = local_context_api.context_router(
        request.query,
        intent=request.intent,
        limit=4,
        current_context=request.current_context,
        max_chars=request.max_chars,
    )
    elapsed_ms = (time.monotonic() - started) * 1000
    _record_local_context_pre_answer_usage(
        request,
        payload,
        mode=mode,
        elapsed_ms=elapsed_ms,
    )
    if mode == "shadow":
        return SourceResult(
            source="local_context",
            ok=True,
            skipped=True,
            aborted_reason="shadow_no_inject",
        )
    if not payload.get("should_inject"):
        return SourceResult(source="local_context", result_count=0)
    return SourceResult(
        source="local_context",
        rendered=str(payload.get("rendered") or ""),
        evidence_refs=[str(ref) for ref in payload.get("evidence_refs") or []],
        result_count=len(payload.get("evidence_refs") or []),
    )


def _local_context_pre_answer_mode() -> str:
    value = (
        os.environ.get("NEXO_PRE_ANSWER_LOCAL_CONTEXT_MODE")
        or os.environ.get("NEXO_LOCAL_CONTEXT_PRE_ANSWER_MODE")
        or "inject"
    )
    clean = str(value or "").strip().lower()
    if clean in {"0", "false", "no", "off", "disabled"}:
        return "off"
    if clean in {"shadow", "observe", "observability", "audit"}:
        return "shadow"
    return "inject"


def _local_context_query_worthwhile(request: SourceRequest) -> bool:
    if request.intent == "file_location":
        return True
    if request.files.strip() or request.area.strip():
        return True
    normalized = _normalize(f"{request.query}\n{request.current_context}")
    if _PATHISH_RE.search(normalized):
        return True
    tokens = _plain_tokens(normalized)
    concept_score = max(
        _feature_score(tokens, _FEATURE_LEXICON[field])
        for field in ("existing_ref", "location", "memory", "modify", "past_work")
    )
    return concept_score >= 0.18


def _record_local_context_skip(request: SourceRequest, *, mode: str, reason: str) -> None:
    try:
        from local_context import usage_events

        usage_events.record_usage_event(
            query=request.query,
            client="pre_answer_router",
            tool="local_context",
            source="local_context",
            route_stage=f"pre_answer:{mode}",
            intent=request.intent,
            result_count=0,
            should_inject=False,
            aborted_reason=reason,
            used_before_response=True,
            metadata={
                "adaptive": reason == "adaptive_skip",
                "current_context_present": bool(request.current_context),
            },
        )
    except Exception:
        return


def _record_local_context_pre_answer_usage(
    request: SourceRequest,
    payload: dict[str, Any],
    *,
    mode: str,
    elapsed_ms: float,
) -> None:
    try:
        from local_context import usage_events

        usage_payload = dict(payload)
        usage_payload["intent"] = request.intent
        usage_payload["should_inject"] = bool(payload.get("should_inject")) and mode == "inject"
        usage_events.record_router_usage(
            request.query,
            usage_payload,
            client="pre_answer_router",
            tool="local_context",
            route_stage=f"pre_answer:{mode}",
            intent=request.intent,
            elapsed_ms=int(max(0.0, elapsed_ms)),
            deadline_ms=0,
            used_before_response=True,
        )
    except Exception:
        return


def _source_filesystem(request: SourceRequest) -> SourceResult:
    root = Path.cwd()
    try:
        completed = subprocess.run(
            ["rg", "--files"],
            cwd=str(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=max(0.05, request.max_chars / 6000),
            check=False,
        )
    except Exception:
        return SourceResult(source="filesystem")
    tokens = set(_plain_tokens(_normalize(request.query)))
    matches = []
    for line in completed.stdout.splitlines():
        path_tokens = set(_plain_tokens(_normalize(line)))
        if tokens & path_tokens:
            matches.append(line)
        if len(matches) >= 8:
            break
    if not matches:
        return SourceResult(source="filesystem")
    return SourceResult(
        source="filesystem",
        rendered="\n".join(f"- {match}" for match in matches),
        evidence_refs=[f"filesystem:{match}" for match in matches],
        result_count=len(matches),
    )


def _source_guard_context(request: SourceRequest) -> SourceResult:
    # Real guard verification: surface the file-conditioned blocking learnings
    # for the requested files. Previously this returned fake evidence
    # (evidence_refs=["guard_context:requested"], result_count=1) WITHOUT any
    # check, which silently satisfied the critical-tier required-source / gap
    # gate for release/server/billing/legal areas. Never fake evidence again.
    files = [f.strip() for f in (request.files or "").split(",") if f.strip()]
    if not files:
        return SourceResult(source="guard_context")
    try:
        from db import get_db
        from plugins.guard import _load_conditioned_learnings
        conn = get_db()
        conditioned = _load_conditioned_learnings(conn, files)
    except Exception:
        # Fail-closed: do NOT fake evidence; report that verification could not run.
        return SourceResult(
            source="guard_context",
            rendered="Guard verification could not run for: " + ", ".join(files),
            result_count=0,
        )
    refs: list[str] = []
    lines: list[str] = []
    for filepath, entries in conditioned.items():
        for entry in entries:
            refs.append(f"learning:{entry.get('id')}")
            lines.append(
                f"- [{entry.get('priority', 'medium')}] {entry.get('title', '')} (applies_to {filepath})"
            )
    if lines:
        return SourceResult(
            source="guard_context",
            rendered="Blocking/file-conditioned learnings:\n" + "\n".join(lines),
            evidence_refs=refs,
            result_count=len(refs),
        )
    # Guard ran and found nothing blocking — a real verified-clean result.
    return SourceResult(
        source="guard_context",
        rendered="Guard verified: no blocking file-conditioned learnings for "
        + ", ".join(files),
        evidence_refs=["guard_context:verified_clean"],
        result_count=0,
    )


def _source_cognitive(request: SourceRequest) -> SourceResult:
    from memory_retrieval import memory_search

    result = memory_search(
        request.query,
        project_hint=request.area,
        depth="evidence",
        limit=4,
        process_queue=True,
    )
    candidates = result.get("candidates") or []
    if not candidates:
        return SourceResult(source="cognitive")
    lines = []
    refs: list[str] = []
    for item in candidates[:4]:
        refs.extend(str(ref) for ref in item.get("evidence_refs") or [])
        lines.append(
            "- "
            + " | ".join(
                part
                for part in (
                    f"type={item.get('type')}" if item.get("type") else "",
                    f"subject={_clip(str(item.get('subject') or ''), 160)}" if item.get("subject") else "",
                    f"summary={_clip(str(item.get('summary') or ''), 320)}" if item.get("summary") else "",
                )
                if part
            )
        )
    return SourceResult(
        source="cognitive",
        rendered=_clip("\n".join(lines), request.max_chars),
        evidence_refs=list(dict.fromkeys(refs)),
        result_count=len(candidates),
    )


def _source_commitments(request: SourceRequest) -> SourceResult:
    from db import list_commitments

    rows = list_commitments(
        query=request.query,
        status="",
        session_id=request.sid if request.intent == "identity_authorship" else "",
        project_key=request.area,
        limit=6,
    )
    if not rows and (
        request.intent == "schedule_commitment"
        or (request.intent == "memory_question" and _cold_commitment_question(request.query))
    ):
        rows = list_commitments(
            query="",
            status="open",
            session_id=request.sid,
            project_key=request.area,
            limit=6,
        )
    return _rows_result(
        "commitments",
        rows,
        ("id", "status", "deadline", "owner", "statement", "action_ref_type", "action_ref_id", "evidence_ref"),
        request.max_chars,
    )


def _source_continuity(request: SourceRequest) -> SourceResult:
    rows = _query_table_like(
        "continuity_snapshots",
        request.query,
        columns=("conversation_id", "session_id", "event_type", "latest_user_text", "latest_assistant_text"),
        limit=4,
    )
    return _rows_result(
        "continuity",
        rows,
        ("conversation_id", "session_id", "event_type", "latest_user_text"),
        request.max_chars,
    )


def _source_reminders(request: SourceRequest) -> SourceResult:
    from db import get_reminders

    rows = get_reminders("all")
    rows = _filter_rows_by_query(rows, request.query, ("id", "description", "date", "status"))[:5]
    return _rows_result("reminders", rows, ("id", "date", "description", "status"), request.max_chars)


def _source_followups(request: SourceRequest) -> SourceResult:
    from db import get_followups

    rows = get_followups("all")
    rows = _filter_rows_by_query(rows, request.query, ("id", "description", "verification", "date", "status"))[:5]
    return _rows_result("followups", rows, ("id", "date", "description", "status"), request.max_chars)


def _source_system_catalog(request: SourceRequest) -> SourceResult:
    try:
        from system_catalog import build_system_catalog
    except Exception:
        return SourceResult(source="system_catalog")
    try:
        catalog = build_system_catalog(limit=80)
    except TypeError:
        catalog = build_system_catalog()
    except Exception:
        return SourceResult(source="system_catalog")
    text = json.dumps(catalog, ensure_ascii=False)
    snippets = _matching_lines(text.splitlines(), request.query, limit=8)
    if not snippets:
        return SourceResult(source="system_catalog")
    return SourceResult(
        source="system_catalog",
        rendered=_clip("\n".join(snippets), request.max_chars),
        evidence_refs=["system_catalog:runtime"],
        result_count=len(snippets),
    )


def _source_runtime_docs(request: SourceRequest) -> SourceResult:
    repo = Path(__file__).resolve().parents[1]
    docs = [
        repo / "docs" / "agent-product-playbook.md",
        repo / "docs" / "product-operator-wiki.md",
        repo / "docs" / "personal-artifacts-manual.md",
        repo / "docs" / "runtime-templates.md",
    ]
    lines: list[str] = []
    for path in docs:
        if not path.is_file():
            continue
        try:
            snippets = _matching_lines(path.read_text(encoding="utf-8", errors="ignore").splitlines(), request.query, limit=2)
        except Exception:
            continue
        for snippet in snippets:
            lines.append(f"{path.name}: {snippet}")
    return SourceResult(
        source="runtime_docs",
        rendered=_clip("\n".join(lines), request.max_chars),
        evidence_refs=[f"runtime_docs:{idx}" for idx, _ in enumerate(lines, start=1)],
        result_count=len(lines),
    )


def _source_source_grep(request: SourceRequest) -> SourceResult:
    repo = Path(__file__).resolve().parents[1]
    terms = [token for token in _plain_tokens(_normalize(request.query)) if len(token) >= 4][:6]
    if not terms:
        return SourceResult(source="source_grep")
    try:
        completed = subprocess.run(
            ["rg", "-n", "--fixed-strings", terms[0], "src", "tests"],
            cwd=str(repo),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=0.6,
            check=False,
        )
    except Exception:
        return SourceResult(source="source_grep")
    rows = completed.stdout.splitlines()[:8]
    return SourceResult(
        source="source_grep",
        rendered=_clip("\n".join(rows), request.max_chars),
        evidence_refs=[f"source_grep:{idx}" for idx, _ in enumerate(rows, start=1)],
        result_count=len(rows),
    )


def _source_runtime_db(request: SourceRequest) -> SourceResult:
    rows = _query_table_like(
        "lifecycle_events",
        request.query,
        columns=("event_type", "client", "payload", "trace_id"),
        limit=4,
    )
    return _rows_result("runtime_db", rows, ("id", "event_type", "client", "created_at"), request.max_chars)


def _query_table_like(table: str, query: str, *, columns: tuple[str, ...], limit: int = 5) -> list[dict[str, Any]]:
    try:
        import db

        conn = db.get_db()
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not table_exists:
            return []
        present = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            if "name" in row.keys()
        }
        usable = [column for column in columns if column in present]
        if not usable:
            return []
        words = [word for word in _plain_tokens(_normalize(query)) if len(word) >= 3][:6]
        if not words:
            return []
        clauses = []
        params: list[str] = []
        for word in words:
            clauses.append("(" + " OR ".join(f"{column} LIKE ?" for column in usable) + ")")
            params.extend([f"%{word}%"] * len(usable))
        sql = f"SELECT * FROM {table} WHERE {' AND '.join(clauses)} LIMIT ?"
        rows = conn.execute(sql, params + [max(1, int(limit))]).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []


def _filter_rows_by_query(rows: Iterable[dict[str, Any]], query: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    terms = {word for word in _plain_tokens(_normalize(query)) if len(word) >= 3}
    if not terms:
        return list(rows)
    matched: list[dict[str, Any]] = []
    for row in rows:
        blob = _normalize(" ".join(str(row.get(field) or "") for field in fields))
        blob_tokens = set(_plain_tokens(blob))
        if terms & blob_tokens or any(term in blob for term in terms):
            matched.append(row)
    return matched


# The resolution-cache versioner (``resolution_cache._SOURCE_VERSIONERS``) looks
# up each cached ref by an EXACT id-column. If ``_rows_result`` builds the ref
# from a DIFFERENT column than the versioner reads, ``ref_version`` resolves to
# the wrong row (or, on a value collision between a free-text id column of one
# row and the numeric id of another, to a REAL but WRONG row) → editing the row
# the ref encodes does not move the snapshot → STALE HIT. The generic
# ``id -> evidence_id -> task_id -> run_id -> session_id -> idx`` chain is exactly
# such a mismatch source: e.g. a ``session_diary`` row carries BOTH ``id`` (the
# versioner column) and ``session_id`` (a free-text column the OLD chain never
# reached because ``id`` won), so the emitted ref and the versioner agreed only
# by luck — and ``lifecycle_events`` has no ``id`` column at all, so the chain
# fell through to ``session_id``/positional ``idx`` while the versioner read
# ``event_id``.
#
# ``_ROUTER_REF_ID_FIELD`` pins, per source, the SINGLE column whose value builds
# the ref. It MUST equal the ``id_column`` the resolution-cache versioner uses
# for that source so ``ref_version('{source}:{id}')`` resolves to the exact row
# the ref encodes. When the pinned column is absent/empty on a row, we emit a
# deliberately positional ``{source}:__row<idx>`` ref: positional refs do not
# match any id-column, so the write gate refuses to cache them (untrackable) —
# never a silent fallback to a colliding column. Sources NOT listed here keep the
# legacy chain (their adapters emit a composite/canonical ref, e.g.
# ``evidence_ledger`` → ``evidence_id``, or are watermark/untrackable anyway).
_ROUTER_REF_ID_FIELD: dict[str, str] = {
    "diary": "id",            # versioner: session_diary.id  (NOT session_id — collides)
    "runtime_db": "event_id", # versioner: lifecycle_events.event_id (no ``id`` column)
}


def _rows_result(
    source: str,
    rows: list[dict[str, Any]],
    fields: tuple[str, ...],
    max_chars: int,
    *,
    id_field: str | None = None,
) -> SourceResult:
    if not rows:
        return SourceResult(source=source)
    pinned = id_field or _ROUTER_REF_ID_FIELD.get(source)
    lines: list[str] = []
    refs: list[str] = []
    for idx, row in enumerate(rows[:5], start=1):
        parts = []
        for field_name in fields:
            value = row.get(field_name)
            if value not in (None, ""):
                parts.append(f"{field_name}={_clip(str(value), 180)}")
        lines.append(f"- " + " | ".join(parts))
        if pinned is not None:
            # Pinned source: the ref MUST use the same column the versioner reads.
            value = row.get(pinned)
            # A positional fallback ref deliberately matches no id-column so the
            # resolution-cache write gate refuses to cache it (untrackable) rather
            # than silently emit a ref under a colliding column.
            ref_id = value if value not in (None, "") else f"__row{idx}"
        else:
            ref_id = row.get("id") or row.get("evidence_id") or row.get("task_id") or row.get("run_id") or row.get("session_id") or idx
        refs.append(f"{source}:{ref_id}")
    return SourceResult(
        source=source,
        rendered=_clip("\n".join(lines), max_chars),
        evidence_refs=refs,
        result_count=len(rows),
    )


def _project_atlas_path() -> Path:
    try:
        import paths

        return paths.brain_dir() / "project-atlas.json"
    except Exception:
        return Path(os.path.expanduser("~/.nexo/brain/project-atlas.json"))


def _matching_lines(lines: Iterable[str], query: str, *, limit: int = 6) -> list[str]:
    terms = {word for word in _plain_tokens(_normalize(query)) if len(word) >= 4}
    if not terms:
        return []
    matches = []
    for line in lines:
        clean = _normalize(line)
        if any(term in clean for term in terms):
            matches.append(_clip(line.strip(), 240))
        if len(matches) >= limit:
            break
    return matches


__all__ = [
    "EVIDENCE_REQUIRED_INTENTS",
    "INJECTING_INTENTS",
    "PRE_ANSWER_INTENTS",
    "IntentClassification",
    "PreAnswerRoute",
    "SourcePlan",
    "SourceRequest",
    "SourceResult",
    "SourceStep",
    "classify_intent",
    "default_source_adapters",
    "plan_sources",
    "redact_secrets",
    "render_evidence_gap",
    "render_route",
    "route_pre_answer",
    "shutdown_executor",
]
