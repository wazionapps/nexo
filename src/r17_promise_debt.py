"""r17_promise_debt — detect assistant promises that were not executed.

Fase 2 Protocol Enforcer Fase D item R17. Plan doc 1 reads:

  SI classifier detecta promesa de acción futura en output al usuario
  Y en los 2 turnos siguientes no hay tool calls relevantes
  ENTONCES flag promise_debt + recordatorio.

Exposes detect_promise(text, classifier) → bool. State (promise window
countdown) lives in the caller — mirrors the R14 / R16 pattern.

Classifier path is the same as R14 / R16: enforcement_classifier.classify
routes through call_model_raw with triple reinforcement. Fail-closed on
any unavailable backend (no promise flagged rather than a false positive).

Mirror: nexo-desktop/lib/r17-promise-debt.js (bundled with Fase D JS
twins at the end of the tranche).
"""
from __future__ import annotations

from core_prompts import render_core_prompt

CLASSIFIER_QUESTION = render_core_prompt("r17-promise-debt-question")

INJECTION_PROMPT_TEMPLATE = render_core_prompt("r17-promise-debt-injection")

DEFAULT_WINDOW_TOOL_CALLS = 2


def detect_promise(assistant_text: str, *, classifier=None) -> bool:
    text = (assistant_text or "").strip()
    if not text:
        return False
    if len(text.split()) < 4:
        # Promises are usually full sentences; single/double word outputs
        # are almost always acknowledgments or status flags.
        return False
    if classifier is None:
        try:
            from enforcement_classifier import classify as classifier  # type: ignore
        except Exception:
            return False
    try:
        return bool(classifier(question=CLASSIFIER_QUESTION, context=text))
    except Exception:
        return False


__all__ = [
    "detect_promise",
    "CLASSIFIER_QUESTION",
    "INJECTION_PROMPT_TEMPLATE",
    "DEFAULT_WINDOW_TOOL_CALLS",
]
