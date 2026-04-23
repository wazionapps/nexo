"""r16_declared_done — detect "I'm done" outputs without nexo_task_close.

Fase 2 Protocol Enforcer Fase C (Capa 2) item R16. Plan doc 1 reads:

  SI classifier detecta que último output afirma tarea completada
  Y hay protocol task abierta
  ENTONCES inyectar obligación task_close con evidence.

Exposes detect_declared_done(assistant_text, classifier=None) → bool and
the reminder prompt template. The window-and-state tracking lives in
the HeadlessEnforcer / Desktop EnforcementEngine, not here.

Classifier contract: same semantic_router yes/no path as R14
(``decision_kind=r16_declared_done``). Fail-closed on unavailable backend →
detect returns False rather than raising.

Mirror: nexo-desktop/lib/r16-declared-done.js (pending, landing in the
next tranche alongside the JS classifier infrastructure).
"""
from __future__ import annotations

from core_prompts import render_core_prompt

CLASSIFIER_QUESTION = render_core_prompt("r16-declared-done-question")
SEMANTIC_LABELS = ("declared_done", "not_done")


INJECTION_PROMPT_TEMPLATE = render_core_prompt("r16-declared-done-injection")


def detect_declared_done(assistant_text: str, *, classifier=None) -> bool:
    """Return True iff the assistant text declares the task complete.

    Short texts (< 3 words) skip the classifier for the same reason R14
    does: the LLM round-trip on "ok." or "done." is noise and the
    down-stream R03 already blocks trivial close evidence.

    Fail-closed on classifier exceptions.
    """
    text = (assistant_text or "").strip()
    if not text:
        return False
    if len(text.split()) < 3:
        return False
    if classifier is None:
        try:
            from semantic_router import route as semantic_route
        except Exception:
            return False
        try:
            result = semantic_route(
                decision_kind="r16_declared_done",
                question=CLASSIFIER_QUESTION,
                context=text,
                labels=SEMANTIC_LABELS,
            )
            return bool(result.ok and (result.label or result.verdict) == "declared_done")
        except Exception:
            return False
    try:
        return bool(classifier(question=CLASSIFIER_QUESTION, context=text))
    except Exception:
        return False


__all__ = [
    "detect_declared_done",
    "CLASSIFIER_QUESTION",
    "INJECTION_PROMPT_TEMPLATE",
]
