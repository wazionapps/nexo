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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable


PRE_ANSWER_INTENTS: tuple[str, ...] = (
    "prior_work",
    "file_location",
    "modify_existing",
    "memory_question",
    "identity_authorship",
    "schedule_commitment",
    "runtime_diagnosis",
    "general",
)

INJECTING_INTENTS = set(PRE_ANSWER_INTENTS) - {"general"}

DEFAULT_BUDGET_MS = 2500
DEFAULT_TOKEN_BUDGET = 2500
MAX_SOURCE_WORKERS = int(os.environ.get("NEXO_PRE_ANSWER_SOURCE_WORKERS", "6") or "6")

_WORD_RE = re.compile(r"[a-z0-9_./:@-]+")
_PLAIN_WORD_RE = re.compile(r"[a-z0-9_]+")
_PATHISH_RE = re.compile(
    r"(?:(?:~|\.{1,2}|/)[\w./@+-]+|[\w.-]+\.(?:py|js|ts|tsx|jsx|md|json|db|sqlite|yml|yaml|txt|csv))"
)
_DATEISH_RE = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}-\d{2}-\d{2})\b")
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
}

_INTENT_FEATURE_WEIGHTS: dict[str, dict[str, float]] = {
    "prior_work": {"past_work": 1.55, "existing_ref": 0.35, "memory": 0.25},
    "file_location": {"location": 1.65, "runtime": 0.20},
    "modify_existing": {"modify": 1.30, "existing_ref": 0.70, "location": 0.20, "past_work": 0.20},
    "memory_question": {"memory": 1.40, "past_work": 0.20, "identity": 0.15},
    "identity_authorship": {"identity": 1.20, "past_work": 0.65},
    "schedule_commitment": {"schedule": 1.55, "past_work": 0.10},
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
    max_chars: int = 1200
    token_budget: int = DEFAULT_TOKEN_BUDGET
    current_context: str = ""


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
            SourceStep("recent_context", timeout_ms=240),
            SourceStep("evidence_ledger", timeout_ms=260),
            SourceStep("protocol_tasks", timeout_ms=240),
            SourceStep("workflows", timeout_ms=260),
            SourceStep("change_log", timeout_ms=260),
            SourceStep("diary", timeout_ms=260),
        ),
        fallback=(
            SourceStep("transcripts", phase="fallback", timeout_ms=700),
            SourceStep("memory", phase="fallback", timeout_ms=400),
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
            SourceStep("project_atlas", timeout_ms=140),
            SourceStep("guard_context", timeout_ms=160),
            SourceStep("change_log", timeout_ms=300),
            SourceStep("workflows", timeout_ms=260),
        ),
        fallback=(
            SourceStep("transcripts", phase="fallback", timeout_ms=650),
            SourceStep("local_context", phase="fallback", timeout_ms=900, max_chars=900),
        ),
    ),
    "memory_question": SourcePlan(
        intent="memory_question",
        primary=(
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
            SourceStep("recent_context", timeout_ms=240),
            SourceStep("evidence_ledger", timeout_ms=260),
            SourceStep("diary", timeout_ms=280),
            SourceStep("change_log", timeout_ms=300),
            SourceStep("transcripts", timeout_ms=700),
        ),
        fallback=(SourceStep("continuity", phase="fallback", timeout_ms=400),),
    ),
    "schedule_commitment": SourcePlan(
        intent="schedule_commitment",
        primary=(
            SourceStep("reminders", timeout_ms=260),
            SourceStep("followups", timeout_ms=260),
            SourceStep("workflows", timeout_ms=280),
        ),
        fallback=(
            SourceStep("diary", phase="fallback", timeout_ms=260),
            SourceStep("transcripts", phase="fallback", timeout_ms=650),
        ),
    ),
    "runtime_diagnosis": SourcePlan(
        intent="runtime_diagnosis",
        primary=(
            SourceStep("system_catalog", timeout_ms=420),
            SourceStep("project_atlas", timeout_ms=160),
            SourceStep("runtime_docs", timeout_ms=300),
        ),
        fallback=(
            SourceStep("source_grep", phase="fallback", timeout_ms=600),
            SourceStep("runtime_db", phase="fallback", timeout_ms=400),
        ),
    ),
    "general": SourcePlan(intent="general"),
}

_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def classify_intent(query: str, *, current_context: str = "") -> IntentClassification:
    """Classify the turn using multilingual concept overlap, not phrase rules."""
    text = f"{query or ''}\n{current_context or ''}".strip()
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
) -> PreAnswerRoute:
    """Route a user turn through pre-answer context sources.

    The function is fail-open: source errors/timeouts become skipped source
    entries, never exceptions that block the user-visible answer.
    """
    started = time.monotonic()
    classification = classify_intent(query, current_context=current_context) if intent == "auto" else _manual_intent(intent)
    source_plan = plan_sources(classification.intent)
    budget = _DeadlineBudget(budget_ms)
    adapters = dict(default_source_adapters())
    adapters.update(source_adapters or {})

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
                    max_chars=step.max_chars,
                    token_budget=token_budget,
                    current_context=current_context,
                )
                result = _run_source_step(adapter, request, step, budget)
                sources.append(result)
                if result.skipped:
                    skipped_sources.append(step.name)
                if result.has_evidence:
                    evidence_refs.extend(result.evidence_refs or [f"{result.source}:inline"])

    elapsed_ms = (time.monotonic() - started) * 1000
    rendered = render_route(
        query=query,
        classification=classification,
        sources=sources,
        token_budget=token_budget,
    )
    should_inject = classification.intent in INJECTING_INTENTS and any(source.has_evidence for source in sources)
    aborted_reason = _route_aborted_reason(sources, budget)
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


def default_source_adapters() -> dict[str, SourceAdapter]:
    return {
        "recent_context": _source_recent_context,
        "evidence_ledger": _source_evidence_ledger,
        "protocol_tasks": _source_protocol_tasks,
        "workflows": _source_workflows,
        "change_log": _source_change_log,
        "diary": _source_diary,
        "transcripts": _source_transcripts,
        "memory": _source_memory,
        "project_atlas": _source_project_atlas,
        "local_context": _source_local_context,
        "filesystem": _source_filesystem,
        "guard_context": _source_guard_context,
        "cognitive": _source_cognitive,
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
) -> dict[str, Any]:
    consulted = [source.source for source in sources if not source.skipped]
    skipped = [source.source for source in sources if source.skipped]
    return {
        "event": "pre_answer_route",
        "query_hash": hashlib.sha256(str(query or "").encode("utf-8", errors="ignore")).hexdigest(),
        "query_preview": redact_secrets(query, max_chars=160),
        "intent": route_intent,
        "confidence": round(float(confidence), 4),
        "should_inject": bool(should_inject),
        "elapsed_ms": round(float(elapsed_ms), 2),
        "budget_ms": int(budget_ms or DEFAULT_BUDGET_MS),
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


def _source_diary(request: SourceRequest) -> SourceResult:
    from db import read_session_diary

    rows = read_session_diary(last_day=True, last_n=6)[:8]
    rows = _filter_rows_by_query(rows, request.query, ("summary", "context", "pending", "mental_state", "session_id"))[:3]
    return _rows_result("diary", rows, ("session_id", "summary", "pending", "created_at"), request.max_chars)


def _source_transcripts(request: SourceRequest) -> SourceResult:
    try:
        from transcript_index import index_recent_transcripts, search_transcript_index

        index_recent_transcripts(hours=72, limit=120, min_user_messages=1)
        indexed_rows = search_transcript_index(request.query, hours=72, limit=4)
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
    except Exception:
        pass

    from tools_transcripts import handle_transcript_search

    rendered = handle_transcript_search(request.query, hours=72, limit=4)
    if rendered.startswith("No transcript matches"):
        return SourceResult(source="transcripts")
    return SourceResult(
        source="transcripts",
        rendered=_clip(rendered, request.max_chars),
        evidence_refs=["transcripts:search"],
        result_count=max(1, rendered.count("\n- ")),
    )


def _source_memory(request: SourceRequest) -> SourceResult:
    from db import recall

    rows = recall(request.query, days=45)[:5]
    return _rows_result("memory", rows, ("source", "title", "snippet", "category"), request.max_chars)


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

    payload = local_context_api.context_router(
        request.query,
        intent=request.intent,
        limit=4,
        current_context=request.current_context,
        max_chars=request.max_chars,
    )
    if not payload.get("should_inject"):
        return SourceResult(source="local_context", result_count=0)
    return SourceResult(
        source="local_context",
        rendered=str(payload.get("rendered") or ""),
        evidence_refs=[str(ref) for ref in payload.get("evidence_refs") or []],
        result_count=len(payload.get("evidence_refs") or []),
    )


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
    # G01 cannot call the MCP guard from this pure core. Return the file scope
    # so G15 can wire real guard context without changing the source plan.
    if not request.files:
        return SourceResult(source="guard_context")
    return SourceResult(
        source="guard_context",
        rendered=f"Guard context requested for files: {request.files}",
        evidence_refs=["guard_context:requested"],
        result_count=1,
    )


def _source_cognitive(request: SourceRequest) -> SourceResult:
    rows = _query_table_like(
        "memory_observations",
        request.query,
        columns=("title", "summary", "content", "source"),
        limit=4,
    )
    return _rows_result("cognitive", rows, ("id", "title", "summary", "source"), request.max_chars)


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


def _rows_result(source: str, rows: list[dict[str, Any]], fields: tuple[str, ...], max_chars: int) -> SourceResult:
    if not rows:
        return SourceResult(source=source)
    lines: list[str] = []
    refs: list[str] = []
    for idx, row in enumerate(rows[:5], start=1):
        parts = []
        for field_name in fields:
            value = row.get(field_name)
            if value not in (None, ""):
                parts.append(f"{field_name}={_clip(str(value), 180)}")
        lines.append(f"- " + " | ".join(parts))
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
    "render_route",
    "route_pre_answer",
]
