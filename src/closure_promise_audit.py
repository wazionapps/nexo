from __future__ import annotations

"""Retrospective closure audit for uncaptured promises and corrections."""

import re
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Literal


SignalKind = Literal["promise", "correction"]
CaptureCheck = Callable[[str], bool]

_PROMISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:lo\s+dejo\s+registrad[oa]|queda\s+registrad[oa])\b", re.I),
    re.compile(r"\b(?:luego|manana|ma[nñ]ana|cuando\s+cierre|al\s+cerrar)\b", re.I),
    re.compile(r"\b(?:te\s+aviso|lo\s+retomo|queda\s+pendiente)\b", re.I),
    re.compile(r"\b(?:I(?:'ll| will)\s+(?:record|follow up|do|check)|later|tomorrow)\b", re.I),
)

_CORRECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:no[, ]+|eso\s+no|te\s+equivocas|est[aá]\s+mal)\b", re.I),
    re.compile(r"\b(?:corrige|correcci[oó]n|no\s+es\s+eso|as[ií]\s+no)\b", re.I),
    re.compile(r"\b(?:wrong|incorrect|that'?s\s+not|you\s+missed)\b", re.I),
)


@dataclass(frozen=True)
class ClosureSignal:
    kind: SignalKind
    text: str
    matched: str
    persisted: bool
    debt: bool
    recommended_action: str
    reason: str


@dataclass(frozen=True)
class ClosureAudit:
    ok: bool
    debt_count: int
    signals: list[ClosureSignal]

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "debt_count": self.debt_count,
            "signals": [asdict(signal) for signal in self.signals],
        }


def audit_closure(
    assistant_text: str,
    user_text: str = "",
    *,
    has_followup_for: CaptureCheck | None = None,
    has_learning_for: CaptureCheck | None = None,
    brain_down: bool = False,
) -> ClosureAudit:
    """Find promises/corrections that were not persisted before closure.

    The audit is fail-closed: if persistence cannot be checked, the signal is
    surfaced as explicit debt instead of being silently ignored.
    """

    signals: list[ClosureSignal] = []
    for text, patterns, kind in (
        (assistant_text, _PROMISE_PATTERNS, "promise"),
        (user_text, _CORRECTION_PATTERNS, "correction"),
    ):
        for matched in _matches(text, patterns):
            signals.append(
                _evaluate_signal(
                    kind=kind,
                    text=text,
                    matched=matched,
                    checker=has_followup_for if kind == "promise" else has_learning_for,
                    brain_down=brain_down,
                )
            )

    debt_count = sum(1 for signal in signals if signal.debt)
    return ClosureAudit(ok=debt_count == 0, debt_count=debt_count, signals=signals)


def _matches(text: str, patterns: Iterable[re.Pattern[str]]) -> list[str]:
    clean = text or ""
    found: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in pattern.finditer(clean):
            value = match.group(0).strip()
            key = value.lower()
            if key not in seen:
                seen.add(key)
                found.append(value)
    return found


def _evaluate_signal(
    *,
    kind: SignalKind,
    text: str,
    matched: str,
    checker: CaptureCheck | None,
    brain_down: bool,
) -> ClosureSignal:
    action = "create_followup" if kind == "promise" else "create_learning"
    if brain_down:
        return ClosureSignal(kind, text, matched, False, True, "mark_debt", "brain_down")
    if checker is None:
        return ClosureSignal(kind, text, matched, False, True, action, "missing_capture_check")
    try:
        persisted = bool(checker(text))
    except Exception as exc:  # pragma: no cover - reason asserted without type coupling
        return ClosureSignal(
            kind,
            text,
            matched,
            False,
            True,
            action,
            f"capture_check_failed:{type(exc).__name__}",
        )
    return ClosureSignal(
        kind,
        text,
        matched,
        persisted,
        not persisted,
        "none" if persisted else action,
        "captured" if persisted else "not_captured",
    )

