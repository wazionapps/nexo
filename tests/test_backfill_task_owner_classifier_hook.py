"""Contracts for the backfill_task_owner classifier hook.

The migration script used to rely on Spanish/English keyword regexes to
infer ``owner`` for legacy followups/reminders. Block D.1 directive
(Francisco 2026-04-22): prefer the local zero-shot classifier for text
intent, keep the regex set only as a fallback for installs without the
model.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.backfill_task_owner import classify  # noqa: E402


@dataclass
class _FakeResult:
    label: str
    confidence: float
    scores: dict
    latency_ms: float = 0.0


class _FakeClassifier:
    """Minimal stub mimicking LocalZeroShotClassifier.classify(text, labels)."""

    def __init__(self, label: str, confidence: float) -> None:
        self._label = label
        self._confidence = confidence

    def classify(self, text: str, labels: Iterable[str]):
        labels = list(labels)
        scores = {l: 0.0 for l in labels}
        scores[self._label] = self._confidence
        return _FakeResult(label=self._label, confidence=self._confidence, scores=scores)


def test_classify_structural_signals_skip_classifier():
    """Structural signals (id prefix, category, recurrence, operator-name)
    stay rule-based and never invoke the LLM path."""
    # NF-PROTOCOL-* always routes to user without the classifier.
    assert classify(
        item_id="NF-PROTOCOL-xyz",
        description="anything at all",
        category="",
        recurrence="",
        user_name="Francisco",
        classifier=_FakeClassifier("agent_automation_cron", 0.99),
    ) == "user"

    # ``category='waiting'`` wins over classifier.
    assert classify(
        item_id="NF-ABC",
        description="anything",
        category="waiting",
        recurrence="",
        user_name="Francisco",
        classifier=_FakeClassifier("agent_automation_cron", 0.99),
    ) == "waiting"

    # Non-empty recurrence routes to agent, classifier ignored.
    assert classify(
        item_id="NF-ABC",
        description="anything",
        category="",
        recurrence="daily",
        user_name="Francisco",
        classifier=_FakeClassifier("user_decision_required", 0.99),
    ) == "agent"


def test_classify_uses_classifier_for_ambiguous_text():
    """On rows without structural signals, a confident classifier verdict
    drives the owner (LLM > regex fallback)."""
    description = (
        "Coordinar con el equipo de soporte externo para que confirmen el "
        "calendario antes del cierre trimestral y publicar resultados."
    )
    assert classify(
        item_id="NF-X",
        description=description,
        category="",
        recurrence="",
        user_name="Francisco",
        classifier=_FakeClassifier("waiting_for_external_response", 0.82),
    ) == "waiting"


def test_classify_falls_back_to_regex_when_classifier_low_confidence():
    """Low-confidence classifier output must defer to the regex ladder."""
    # Description triggers the waiting regex — classifier below floor must
    # not override it.
    assert classify(
        item_id="NF-X",
        description="Esperando respuesta de Maria sobre el presupuesto.",
        category="",
        recurrence="",
        user_name="",
        classifier=_FakeClassifier("user_decision_required", 0.30),
    ) == "waiting"


def test_classify_falls_back_when_classifier_missing():
    """No classifier at all ⇒ regex ladder. Plain imperatives still route
    to user; agent keywords still route to agent; unknown text lands in
    shared."""
    assert classify(
        item_id="NF-X",
        description="revisar implementación del módulo nuevo",
        category="",
        recurrence="",
        user_name="",
        classifier=None,
    ) == "user"
    assert classify(
        item_id="NF-X",
        description="cron que monitoriza la cola cada minuto",
        category="",
        recurrence="",
        user_name="",
        classifier=None,
    ) == "agent"
    assert classify(
        item_id="NF-X",
        description="nota corta sin señales fuertes",
        category="",
        recurrence="",
        user_name="",
        classifier=None,
    ) == "shared"


def test_classify_skips_classifier_for_very_short_text():
    """The classifier hook ignores texts shorter than the noise floor so
    the migration does not burn an LLM call on one-liners."""
    # Even a "high-confidence" classifier verdict must be skipped because
    # the text is below the 40-character floor; regex fallback applies.
    assert classify(
        item_id="NF-X",
        description="ok",
        category="",
        recurrence="",
        user_name="",
        classifier=_FakeClassifier("waiting_for_external_response", 0.99),
    ) == "shared"


def test_classify_user_name_signal_wins_over_classifier():
    """Explicit ``<OperatorName> decide/revisa/aprueba`` takes priority."""
    assert classify(
        item_id="NF-X",
        description=(
            "Francisco decide si escalamos el presupuesto o pausamos la campaña "
            "hasta recibir más datos de conversión del equipo externo."
        ),
        category="",
        recurrence="",
        user_name="Francisco",
        classifier=_FakeClassifier("agent_automation_cron", 0.95),
    ) == "user"
