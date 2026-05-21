"""r14_correction_learning — detect user corrections and demand a learning_add.

Phase 2 Protocol Enforcer Phase C (Layer 2) item R14. Plan doc 1 reads:

  IF the last user message -> cognitive_sentiment.is_correction = true
     OR valence < -0.4
  AND nexo_learning_add does NOT appear in the next 3 tool calls
  THEN inject the obligation.

Implementation contract:

  - Correction detection goes through semantic_router decision_kind
    ``r14_correction``. Learning #122 prohibits keyword-based semantic
    detection; the router path is the sanctioned alternative.
  - Fail-closed: when the classifier is unavailable (no API key,
    automation_backend=none, timeout, 5xx), is_correction returns
    False. Downstream R28 (system prompt) and the auto_capture hook
    still cover the gap; we would rather miss a correction than
    harass the agent with false-positive R14 injections.
  - The "3-tool-calls window" state lives in the caller
    (HeadlessEnforcer / Desktop EnforcementEngine). This module only
    exposes the pure decision function and the structured injection
    prompt.

Mirror: nexo-desktop/lib/r14-correction-learning.js (pending; see
docs/client-parity-checklist.md).
"""
from __future__ import annotations

from core_prompts import render_core_prompt

CLASSIFIER_QUESTION = render_core_prompt("r14-correction-learning-question")
SEMANTIC_LABELS = ("negative_feedback", "ordinary_request")
POSITIVE_LABEL = "negative_feedback"


INJECTION_PROMPT_TEMPLATE = render_core_prompt("r14-correction-learning-injection")


DEFAULT_WINDOW_TOOL_CALLS = 3


def detect_correction(user_text: str, *, classifier=None) -> bool:
    """Return True iff the user message is a correction.

    Args:
        user_text: Raw user-role text from the stream.
        classifier: Injection point for tests. Defaults to
            semantic_router.route(decision_kind="r14_correction").

    Fail-closed on ClassifierUnavailableError — returns False rather
    than raising so the caller's enforcement loop never crashes on a
    backend outage.
    """
    text = (user_text or "").strip()
    if not text:
        return False
    # Very short messages ("ok", "gracias", "lol") are almost never
    # corrections and calling the LLM for every one is noise. We still
    # route them through the classifier when they contain more than
    # a couple of words so non-Latin scripts get fair treatment.
    if len(text.split()) < 2:
        return False
    if classifier is None:
        try:
            from semantic_router import route as semantic_route
        except Exception:
            return False
        try:
            result = semantic_route(
                decision_kind="r14_correction",
                question=CLASSIFIER_QUESTION,
                context=text,
                labels=SEMANTIC_LABELS,
            )
            return bool(result.ok and (result.label or result.verdict) == POSITIVE_LABEL)
        except Exception:
            return False
    try:
        return bool(classifier(question=CLASSIFIER_QUESTION, context=text))
    except Exception:
        # Classifier unavailable / timeout / bad config — stay silent.
        return False


__all__ = [
    "detect_correction",
    "CLASSIFIER_QUESTION",
    "INJECTION_PROMPT_TEMPLATE",
    "DEFAULT_WINDOW_TOOL_CALLS",
]
