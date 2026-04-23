"""Detect user messages that semantically close the active session.

This is a small classifier-based helper shared by the Brain runtime so
session-end handling does not depend on visible-language keyword lists.
"""
from __future__ import annotations

from core_prompts import render_core_prompt

CLASSIFIER_QUESTION = render_core_prompt("session-end-intent-question")
SEMANTIC_LABELS = ("session_end", "continue_session")


def detect_session_end_intent(user_text: str, *, classifier=None) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    if classifier is None:
        try:
            from semantic_router import route as semantic_route
        except Exception:
            return False
        try:
            result = semantic_route(
                decision_kind="session_end_intent",
                question=CLASSIFIER_QUESTION,
                context=text,
                labels=SEMANTIC_LABELS,
            )
            return bool(result.ok and (result.label or result.verdict) == "session_end")
        except Exception:
            return False
    try:
        return bool(classifier(question=CLASSIFIER_QUESTION, context=text))
    except Exception:
        return False


__all__ = [
    "detect_session_end_intent",
    "CLASSIFIER_QUESTION",
]
