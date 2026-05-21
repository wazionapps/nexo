"""r17_promise_debt — detect assistant promises that were not executed.

Phase 2 Protocol Enforcer Phase D item R17. Plan doc 1 reads:

  IF the classifier detects a future-action promise in user-facing output
  AND the next 2 turns have no relevant tool calls
  THEN flag promise_debt + reminder.

Exposes detect_promise(text, classifier) → bool. State (promise window
countdown) lives in the caller — mirrors the R14 / R16 pattern.

Classifier path is the same as R14 / R16:
semantic_router decision_kind ``r17_promise_debt``. Fail-closed on any
unavailable backend (no promise flagged rather than a false positive).

Mirror: nexo-desktop/lib/r17-promise-debt.js (bundled with Phase D JS
twins at the end of the tranche).
"""
from __future__ import annotations

from core_prompts import render_core_prompt

CLASSIFIER_QUESTION = render_core_prompt("r17-promise-debt-question")
SEMANTIC_LABELS = ("promise", "no_promise")

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
            from semantic_router import route as semantic_route
        except Exception:
            return False
        try:
            result = semantic_route(
                decision_kind="r17_promise_debt",
                question=CLASSIFIER_QUESTION,
                context=text,
                labels=SEMANTIC_LABELS,
            )
            return bool(result.ok and (result.label or result.verdict) == "promise")
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
