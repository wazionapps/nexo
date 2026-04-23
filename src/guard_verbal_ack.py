"""Detect explicit user approval for a single-file guard-blocked task.

This keeps the bridge narrow: it is only meant to unlock a task that is
already blocked by guard rules, never to act as a generic "yes means allow
anything" bypass.
"""
from __future__ import annotations

from core_prompts import render_core_prompt


CLASSIFIER_QUESTION = render_core_prompt("guard-verbal-ack-question")
SEMANTIC_LABELS = ("explicit_ack", "not_ack")


def _build_context(
    user_text: str,
    *,
    task_type: str = "",
    goal: str = "",
    file_path: str = "",
    guard_summary: str = "",
) -> str:
    sections = [
        f"User message:\n{(user_text or '').strip()}",
        f"Task type:\n{(task_type or '').strip()}",
        f"Task goal:\n{(goal or '').strip()}",
        f"Single blocked file:\n{(file_path or '').strip()}",
        f"Guard summary:\n{(guard_summary or '').strip()}",
    ]
    return "\n\n".join(part for part in sections if part.strip())


def detect_guard_verbal_ack(
    user_text: str,
    *,
    task_type: str = "",
    goal: str = "",
    file_path: str = "",
    guard_summary: str = "",
    classifier=None,
) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    if classifier is None:
        try:
            from semantic_router import route as semantic_route
        except Exception:
            return False
    context = _build_context(
        text,
        task_type=task_type,
        goal=goal,
        file_path=file_path,
        guard_summary=guard_summary,
    )
    if classifier is None:
        try:
            result = semantic_route(
                decision_kind="guard_verbal_ack",
                question=CLASSIFIER_QUESTION,
                context=context,
                labels=SEMANTIC_LABELS,
            )
            return bool(result.ok and (result.label or result.verdict) == "explicit_ack")
        except Exception:
            return False
    try:
        return bool(classifier(question=CLASSIFIER_QUESTION, context=context))
    except Exception:
        return False


__all__ = [
    "detect_guard_verbal_ack",
    "CLASSIFIER_QUESTION",
]
