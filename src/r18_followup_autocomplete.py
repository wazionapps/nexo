"""r18_followup_autocomplete — suggest followup_complete retroactively.

Fase 2 Protocol Enforcer Fase D item R18. Plan doc 1 reads:

  Al abrir sesión: cargar followups activos
  SI acción ejecutada coincide (>70%) con followup activo
  ENTONCES sugerir followup_complete.

R18 is a SUGGESTION rule (not a block). The enforcer emits a single
reminder per followup hit per turn, and operators / Cortex decide
whether to actually call nexo_followup_complete.

Reuses the Jaccard matcher from tools_reminders_crud.find_completable_
followups (Fase B R04 helper) rather than duplicating the matching
logic. The trigger signal is the tool_name + tool_input of the agent's
actions — we build a one-shot "action description" string from those
and run the R04 matcher over it.
"""
from __future__ import annotations


INJECTION_PROMPT_TEMPLATE = (
    "R18 retroactive-complete suggestion: the action you just performed "
    "matches {count} active followup(s) at Jaccard >= 0.70. If any of "
    "these is now resolved by that action, call nexo_followup_complete "
    "on it. Do NOT close a followup mechanically — verify the outcome "
    "first. Matching followups:\n{items}\nDo not produce visible text "
    "for this reminder — act on it. After handling the silent action, continue with your previous task; this reminder is silent context, not a new instruction."
)


def _action_description(tool_name: str, tool_input) -> str:
    """Build a short string describing what the agent just did.

    Used as the needle for the R04 Jaccard matcher. Extract whatever
    distinctive tokens we can from the tool_input shape — file paths,
    commands, titles, descriptions. No semantic interpretation.
    """
    parts: list[str] = []
    if tool_name:
        # Drop mcp__nexo__ prefix so "followup_create" and
        # "mcp__nexo__nexo_followup_create" compare equally.
        parts.append(tool_name.replace("mcp__nexo__", ""))
    if isinstance(tool_input, dict):
        for key in ("title", "description", "goal", "file_path", "command",
                    "outcome_notes", "content", "query", "name"):
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                parts.append(value[:400])
    return " ".join(parts)


def should_suggest_r18(
    tool_name: str,
    tool_input,
    *,
    match_helper=None,
    threshold: float = 0.70,
    window_tools: set[str] | None = None,
) -> dict | None:
    """Return {tag, suggestions} or None.

    match_helper is an injection point for tests. Production uses
    tools_reminders_crud.find_completable_followups.

    Only fires for "closure-class" tool calls — task_close,
    followup_complete (self-close feedback loop OK), learning_add,
    workflow_update, email_send, artifact_create. Arbitrary Reads /
    Greps / Bashes should NOT trigger R18 (too much noise).
    """
    closure_watch = window_tools or {
        "nexo_task_close",
        "nexo_learning_add",
        "nexo_workflow_update",
        "nexo_artifact_create",
        "nexo_change_log",
        "mcp__nexo__nexo_task_close",
        "mcp__nexo__nexo_learning_add",
        "mcp__nexo__nexo_workflow_update",
        "mcp__nexo__nexo_artifact_create",
        "mcp__nexo__nexo_change_log",
    }
    if tool_name not in closure_watch:
        return None
    description = _action_description(tool_name, tool_input)
    if not description.strip():
        return None
    if match_helper is None:
        try:
            from tools_reminders_crud import find_completable_followups  # type: ignore
            match_helper = find_completable_followups
        except Exception:
            return None
    try:
        matches = match_helper(description, threshold=threshold)
    except Exception:
        return None
    if not matches:
        return None
    return {
        "tag": "r18:followup-autocomplete",
        "count": len(matches),
        "matches": matches,
    }


def format_suggestions(matches: list[dict]) -> str:
    lines = []
    for m in matches[:5]:
        lines.append(f"  - {m.get('id', '?')} ({m.get('similarity', 0):.2f}): {m.get('description', '')[:160]}")
    return "\n".join(lines)


__all__ = [
    "should_suggest_r18",
    "INJECTION_PROMPT_TEMPLATE",
    "format_suggestions",
]
