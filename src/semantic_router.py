"""semantic_router — Plan ONEPASS LLM Coverage.

Central router for every model-backed semantic decision in NEXO Brain. Call
sites declare a *decision_kind* and pass question/context; the router
applies the policy for that kind and dispatches through the stack:

    fast_local  ->  semantic_reasoner  ->  remote_fallback

Design contract (from ~/Desktop/NEXO-ONEPASS-LLM-COVERAGE-RELEASE-PLAN.md):

- Brain owns the semantic contract, model pins and routing policy.
- Every call site passes a *named* decision_kind; policy lives here, not in
  the caller. This replaces the previous pattern where each caller invented
  its own policy tree.
- The existing ``LocalZeroShotClassifier`` stays as the cheap multilingual
  first pass (``fast_local``).
- ``semantic_reasoner`` is the second, stronger layer. Its implementation
  lives in ``src/semantic_reasoner.py`` with two modes: Mode A (strict
  multi-pass over the same local classifier with tighter thresholds) and
  Mode B (LLM-cached reasoner for code-aware decisions).
- ``remote_fallback`` is the existing ``call_model_raw`` chain. It is no
  longer the default path for local-friendly decisions; it only fires if
  the upstream layers refuse or degrade.

The router returns a ``RouterResult`` dataclass so callers can inspect
which route was used, whether degraded mode is active, and what confidence
the decision carries. This is also what Desktop will consume via the
``brain-semantic-router.js`` bridge shipped in the companion PR.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Contract dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RouterResult:
    """Outcome of a ``route()`` call.

    Fields match the minimum contract documented in the plan (section
    "Minimum router output contract"):

      - ``ok``: overall success (at least one layer produced a decision)
      - ``decision_kind``: the kind the caller passed
      - ``verdict``: the chosen label when the caller used zero-shot
        classification; None when the underlying layer returned free text
      - ``label``: alias for ``verdict`` to match the plan's wording; kept
        consistent to simplify Desktop bridge mapping
      - ``confidence``: [0.0, 1.0]
      - ``route_used``: one of ``fast_local``, ``semantic_reasoner``,
        ``remote_fallback``, or ``no_route`` when every layer refused
      - ``degraded``: True when the chosen layer could not meet its normal
        bar (fallback fired, stricter threshold not met, cache-only, etc.)
      - ``error``: short human-readable reason when ``ok`` is False
      - ``meta``: free-form layer-specific evidence (scores dict, cache
        key, latency, model id) — Desktop uses it for telemetry
    """

    ok: bool
    decision_kind: str
    verdict: str | None = None
    label: str | None = None
    confidence: float = 0.0
    route_used: str = "no_route"
    degraded: bool = False
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Decision kinds + policy table
# ---------------------------------------------------------------------------
#
# The plan enumerates 18 decision_kinds that need to route through here. They
# fall into two families:
#
#   TEXTUAL     — the first-line local classifier is good enough; the
#                 reasoner adds a stricter multi-pass check for ambiguous
#                 cases. Remote is only a last-resort safety net.
#
#   CODE_AWARE  — the fast local classifier is not designed for code-aware
#                 semantics (T4 R15/R23e/R23f/R23h, r20). The reasoner
#                 routes those straight to a cached LLM call.
#
# Any decision_kind not listed here falls through to remote_fallback with
# ``degraded=True`` to make accidental misuse visible in telemetry instead
# of silent.
#
# Keep this map in lockstep with ``docs/semantic-reasoner-model-notes.md``.


TEXTUAL_KINDS: tuple[str, ...] = (
    "session_end_intent",
    "autonomy_mandate",
    "guard_verbal_ack",
    "r14_correction",
    "r16_declared_done",
    "r17_promise_debt",
    "r34_identity_coherence",
    "followup_operator_attention",
    "drive_signal_type",
    "drive_area",
    "reply_event_type",
    "query_intent",
    "sentiment_intent",
)


CODE_AWARE_KINDS: tuple[str, ...] = (
    "r20_constant_change",
    "t4_r15",
    "t4_r23e",
    "t4_r23f",
    "t4_r23h",
)


ALL_DECISION_KINDS: tuple[str, ...] = TEXTUAL_KINDS + CODE_AWARE_KINDS


# Per-kind policy. Explicit, human-readable, no defaults that silently
# expand coverage. Changing policy = editing this dict + updating the
# model-notes doc + bumping tests.
_POLICY: dict[str, dict[str, Any]] = {
    kind: {
        "family": "textual",
        "fast_local_threshold": 0.60,
        "reasoner_mode": "multipass_local",
        "reasoner_threshold": 0.75,
        "allow_remote_fallback": True,
    }
    for kind in TEXTUAL_KINDS
}

_POLICY.update(
    {
        kind: {
            "family": "code_aware",
            "fast_local_threshold": None,  # skip fast_local
            "reasoner_mode": "cached_llm",
            "reasoner_threshold": 0.60,
            "allow_remote_fallback": True,
        }
        for kind in CODE_AWARE_KINDS
    }
)


def policy_for(decision_kind: str) -> dict[str, Any] | None:
    """Return the policy entry for a kind, or None if unknown."""
    return _POLICY.get(decision_kind)


# ---------------------------------------------------------------------------
# Layer adapters
# ---------------------------------------------------------------------------
#
# The router does not import the heavy modules at the top of the file so
# that a caller who only wants ``policy_for`` or ``ALL_DECISION_KINDS`` does
# not pay the import cost. The adapters below resolve the dependencies
# lazily and wrap failures as ``None`` so the router can advance to the
# next layer deterministically.


def _run_fast_local(
    *,
    question: str,
    labels: tuple[str, ...],
    confidence_floor: float,
) -> RouterResult | None:
    """Try ``LocalZeroShotClassifier``. Return None on unavailable or
    below-threshold so the router advances."""
    try:
        from classifier_local import LocalZeroShotClassifier
    except Exception as exc:  # pragma: no cover — install not ready
        _logger.debug("semantic_router: classifier_local unavailable (%s)", exc)
        return None

    clf = LocalZeroShotClassifier(confidence_floor=confidence_floor)
    result = clf.classify(question, labels)
    if result is None:
        return None
    if result.confidence < confidence_floor:
        return None

    return RouterResult(
        ok=True,
        decision_kind="",  # filled by caller
        verdict=result.label,
        label=result.label,
        confidence=float(result.confidence),
        route_used="fast_local",
        degraded=False,
        meta={
            "scores": dict(result.scores),
            "latency_ms": float(result.latency_ms),
            "threshold": confidence_floor,
        },
    )


def _run_semantic_reasoner(
    *,
    decision_kind: str,
    question: str,
    labels: tuple[str, ...] | None,
    context: str,
    mode: str,
    confidence_floor: float,
) -> RouterResult | None:
    """Delegate to ``src/semantic_reasoner.py``. Return None on unavailable
    so the router advances to remote_fallback."""
    try:
        from semantic_reasoner import reason
    except Exception as exc:  # pragma: no cover
        _logger.debug("semantic_router: semantic_reasoner unavailable (%s)", exc)
        return None

    try:
        return reason(
            decision_kind=decision_kind,
            question=question,
            labels=labels,
            context=context,
            mode=mode,
            confidence_floor=confidence_floor,
        )
    except Exception as exc:  # noqa: BLE001 — fail-closed, degrade to remote
        _logger.warning("semantic_reasoner.reason raised: %s", exc)
        return None


def _run_remote_fallback(
    *,
    decision_kind: str,
    question: str,
    labels: tuple[str, ...] | None,
    context: str,
) -> RouterResult | None:
    """Last-resort LLM call via ``call_model_raw``. The router marks the
    result as ``degraded=True`` so telemetry shows when the stack fell
    through."""
    try:
        from call_model_raw import ClassifierUnavailableError, call_model_raw
    except Exception as exc:  # pragma: no cover
        _logger.debug("semantic_router: call_model_raw unavailable (%s)", exc)
        return None

    prompt = _build_remote_prompt(
        decision_kind=decision_kind,
        question=question,
        labels=labels,
        context=context,
    )
    system = (
        "You are NEXO's remote semantic fallback. Answer with the single "
        "best label from the provided list, or with 'unknown' if none fit. "
        "No prose, no explanation."
    )

    try:
        raw = call_model_raw(
            prompt,
            system=system,
            caller="semantic_reasoner",
            tier="muy_bajo",
            max_tokens=32,
            temperature=0.0,
        )
    except ClassifierUnavailableError as exc:
        return RouterResult(
            ok=False,
            decision_kind=decision_kind,
            route_used="remote_fallback",
            degraded=True,
            error=f"remote_unavailable: {exc}",
        )

    verdict = _normalize_remote_answer(raw, labels)
    return RouterResult(
        ok=verdict is not None,
        decision_kind=decision_kind,
        verdict=verdict,
        label=verdict,
        confidence=0.55 if verdict is not None else 0.0,
        route_used="remote_fallback",
        degraded=True,  # always degraded relative to the local-first ideal
        meta={"raw_response": raw[:120]},
    )


def _build_remote_prompt(
    *,
    decision_kind: str,
    question: str,
    labels: tuple[str, ...] | None,
    context: str,
) -> str:
    parts = [
        f"Decision kind: {decision_kind}",
        f"Question: {question}",
    ]
    if context:
        parts.append(f"Context: {context[:400]}")
    if labels:
        parts.append("Candidate labels: " + ", ".join(labels))
        parts.append("Reply with exactly one of the labels above.")
    else:
        parts.append("Reply with the shortest phrase that answers the question.")
    return "\n".join(parts)


def _normalize_remote_answer(
    raw: str, labels: tuple[str, ...] | None
) -> str | None:
    text = (raw or "").strip().lower()
    if not text:
        return None
    if labels:
        for label in labels:
            if label.lower() == text:
                return label
        for label in labels:
            if label.lower() in text:
                return label
        return None
    return text


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def route(
    *,
    decision_kind: str,
    question: str,
    context: str = "",
    labels: tuple[str, ...] | list[str] | None = None,
    allow_remote_fallback: bool = True,
) -> RouterResult:
    """Route a semantic decision through the stack.

    The caller names the *kind* of decision. The router looks up the policy,
    dispatches through fast_local -> semantic_reasoner -> remote_fallback,
    and returns the first layer that produced a decision above its
    threshold.

    ``allow_remote_fallback=False`` forces local-only behaviour; the router
    will return ``ok=False, route_used='no_route'`` if every local layer
    refused. Useful for strict-offline automation or pytest.
    """
    policy = policy_for(decision_kind)
    if policy is None:
        return RouterResult(
            ok=False,
            decision_kind=decision_kind,
            route_used="no_route",
            degraded=True,
            error=f"unknown decision_kind: {decision_kind}",
        )

    labels_tuple: tuple[str, ...] | None = (
        tuple(labels) if labels else None
    )

    # Step 1 — fast_local for textual families only.
    if policy["fast_local_threshold"] is not None and labels_tuple:
        fast = _run_fast_local(
            question=question,
            labels=labels_tuple,
            confidence_floor=float(policy["fast_local_threshold"]),
        )
        if fast is not None:
            fast.decision_kind = decision_kind
            return fast

    # Step 2 — semantic_reasoner (Mode A or B depending on policy).
    reasoned = _run_semantic_reasoner(
        decision_kind=decision_kind,
        question=question,
        labels=labels_tuple,
        context=context,
        mode=str(policy["reasoner_mode"]),
        confidence_floor=float(policy["reasoner_threshold"]),
    )
    if reasoned is not None and reasoned.ok:
        return reasoned

    # Step 3 — remote_fallback if allowed.
    if allow_remote_fallback and policy.get("allow_remote_fallback", True):
        remote = _run_remote_fallback(
            decision_kind=decision_kind,
            question=question,
            labels=labels_tuple,
            context=context,
        )
        if remote is not None:
            return remote

    return RouterResult(
        ok=False,
        decision_kind=decision_kind,
        route_used="no_route",
        degraded=True,
        error="every layer refused or was unavailable",
    )


__all__ = [
    "ALL_DECISION_KINDS",
    "CODE_AWARE_KINDS",
    "RouterResult",
    "TEXTUAL_KINDS",
    "policy_for",
    "route",
]
