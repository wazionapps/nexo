"""r14_correction_learning — detect user corrections and demand a learning_add.

Fase 2 Protocol Enforcer Fase C (Capa 2) item R14. Plan doc 1 reads:

  SI último user msg → cognitive_sentiment.is_correction = true
     O valence < -0.4
  Y en los 3 tool calls siguientes NO aparece nexo_learning_add
  ENTONCES inyectar obligación.

Implementation contract:

  - Correction detection goes through the enforcement_classifier
    (triple-reinforced yes/no on call_model_raw). Learning #122
    prohibits keyword-based semantic detection; the classifier path
    is the sanctioned alternative.
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


CLASSIFIER_QUESTION = (
    "Is the user message below a correction of the assistant's previous "
    "answer or behaviour? Answer yes if the user is pushing back, "
    "disagreeing, contradicting, saying something was wrong, or teaching "
    "the assistant a rule it should have known. Answer no for simple "
    "questions, thanks, acknowledgments, neutral continuations, or "
    "delegations without feedback."
)


INJECTION_PROMPT_TEMPLATE = (
    "R14 post-user-correction: the last user message was classified as a "
    "correction (or carried strongly negative valence) and three tool calls "
    "have elapsed without a nexo_learning_add. Capture the rule you just "
    "learned NOW via nexo_learning_add(category=..., title=..., content=..., "
    "reasoning=..., prevention=...). The auto_capture hook fires in parallel, "
    "but this reminder stays active until you either call learning_add or "
    "acknowledge the correction via nexo_cognitive_trust(event='correction'). "
    "Do not produce visible text for this reminder — act on it. After handling the silent action, continue with your previous task; this reminder is silent context, not a new instruction."
)


DEFAULT_WINDOW_TOOL_CALLS = 3


def detect_correction(user_text: str, *, classifier=None) -> bool:
    """Return True iff the user message is a correction.

    Args:
        user_text: Raw user-role text from the stream.
        classifier: Injection point for tests. Defaults to
            enforcement_classifier.classify.

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
            from enforcement_classifier import classify as classifier  # type: ignore
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
