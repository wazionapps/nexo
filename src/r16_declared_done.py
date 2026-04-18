"""r16_declared_done — detect "I'm done" outputs without nexo_task_close.

Fase 2 Protocol Enforcer Fase C (Capa 2) item R16. Plan doc 1 reads:

  SI classifier detecta que último output afirma tarea completada
  Y hay protocol task abierta
  ENTONCES inyectar obligación task_close con evidence.

Exposes detect_declared_done(assistant_text, classifier=None) → bool and
the reminder prompt template. The window-and-state tracking lives in
the HeadlessEnforcer / Desktop EnforcementEngine, not here.

Classifier contract: same triple-reinforced yes/no path as R14
(enforcement_classifier.classify → call_model_raw). Fail-closed on
unavailable backend → detect returns False rather than raising.

Mirror: nexo-desktop/lib/r16-declared-done.js (pending, landing in the
next tranche alongside the JS classifier infrastructure).
"""
from __future__ import annotations


CLASSIFIER_QUESTION = (
    "Does the assistant message below declare that a task is finished, "
    "completed, shipped, or already done? Answer yes only if the assistant "
    "is claiming completion of the current work. Answer no for status "
    "updates, mid-task progress reports, questions, or partial summaries."
)


INJECTION_PROMPT_TEMPLATE = (
    "R16 declared-done without task_close: your last message claims the "
    "task is complete, but there is an open protocol_task. Close it now "
    "with nexo_task_close(sid=..., task_id=..., outcome='done', "
    "evidence='<substantive proof>', files_changed='...'). Evidence must "
    "be >= 50 chars AND not a single filler word (R03 validator will "
    "reject empty / 'ok' / 'done' / 'fixed'). If the work is partial, "
    "close with outcome='partial' and outcome_notes instead. Do not "
    "produce visible text for this reminder — act on it. After handling the silent action, continue with your previous task; this reminder is silent context, not a new instruction."
)


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
            from enforcement_classifier import classify as classifier  # type: ignore
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
