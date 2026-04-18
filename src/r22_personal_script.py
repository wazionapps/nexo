"""r22_personal_script — demand context lookup before creating a personal script.

Fase 2 Protocol Enforcer Fase D item R22. Plan doc 1 reads:

  SI intent nexo_personal_script_create O Write sobre scripts/
  Y no hubo personal_scripts_list + skill_match + learning_search
  ENTONCES inyectar obligación.

R22 fires BEFORE introducing a new personal script to make sure the
agent first checked:
  - Is there an existing personal script doing this? (personal_scripts_list)
  - Is there a reusable skill? (skill_match)
  - Are there learnings saying "don't do X"? (learning_search)

Structural check against recent_tool_records, no LLM.
"""
from __future__ import annotations


INJECTION_PROMPT_TEMPLATE = (
    "R22 pre-personal-script: you are about to create/modify a personal "
    "script ({path}) without first checking: (1) does an existing personal "
    "script cover this via nexo_personal_scripts_list, (2) does a reusable "
    "skill apply via nexo_skill_match, (3) is there a blocking learning "
    "via nexo_learning_search. Run those three probes, surface any hit, "
    "and only then proceed. Do not produce visible text for this "
    "reminder — act on it. After handling the silent action, continue with your previous task; this reminder is silent context, not a new instruction."
)


_WATCHED_CREATE_TOOLS = frozenset({
    "nexo_personal_script_create",
    "mcp__nexo__nexo_personal_script_create",
})

_WATCHED_WRITE_TOOLS = frozenset({
    "Edit", "Write", "MultiEdit",
    "mcp__nexo__Edit", "mcp__nexo__Write", "mcp__nexo__MultiEdit",
})

_SCRIPTS_PATH_TOKENS = (
    "/.nexo/scripts/",
    "/.nexo/personal_scripts/",
    "personal_scripts/",
)

_CONTEXT_TOOLS = frozenset({
    "nexo_personal_scripts_list",
    "mcp__nexo__nexo_personal_scripts_list",
    "nexo_skill_match",
    "mcp__nexo__nexo_skill_match",
    "nexo_learning_search",
    "mcp__nexo__nexo_learning_search",
})


def _is_personal_script_write(tool_name: str, tool_input) -> tuple[bool, str]:
    if tool_name in _WATCHED_CREATE_TOOLS:
        path = ""
        if isinstance(tool_input, dict):
            path = str(tool_input.get("name") or tool_input.get("path") or "")
        return True, path
    if tool_name in _WATCHED_WRITE_TOOLS and isinstance(tool_input, dict):
        path = str(tool_input.get("file_path") or tool_input.get("path") or "")
        if any(token in path for token in _SCRIPTS_PATH_TOKENS):
            return True, path
    return False, ""


def _recent_context_probes(recent_tool_records, window_calls: int = 20) -> set[str]:
    """Return the set of context tools invoked in the recent window."""
    if not recent_tool_records:
        return set()
    seen: set[str] = set()
    scanned = 0
    for record in reversed(recent_tool_records):
        if scanned >= window_calls:
            break
        scanned += 1
        tool = str(getattr(record, "tool", "") or "")
        if tool in _CONTEXT_TOOLS:
            seen.add(tool.replace("mcp__nexo__", ""))
    return seen


def should_inject_r22(
    tool_name: str,
    tool_input,
    recent_tool_records,
) -> dict | None:
    is_script_write, path = _is_personal_script_write(tool_name, tool_input)
    if not is_script_write:
        return None
    probes = _recent_context_probes(recent_tool_records)
    required = {
        "nexo_personal_scripts_list",
        "nexo_skill_match",
        "nexo_learning_search",
    }
    if required.issubset(probes):
        return None  # all three context probes happened recently
    return {
        "tag": f"r22:{path or 'unknown'}",
        "path": path or "unknown",
        "missing": sorted(required - probes),
    }


__all__ = [
    "should_inject_r22",
    "INJECTION_PROMPT_TEMPLATE",
]
