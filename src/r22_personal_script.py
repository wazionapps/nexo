"""r22_personal_script — demand context lookup before creating a personal script.

Phase 2 Protocol Enforcer Phase D item R22. Plan doc 1 reads:

  IF the intent is nexo_personal_script_create OR Write under scripts/
  AND there was no personal_scripts_list + skill_match + learning_search
  THEN inject the obligation.

R22 fires BEFORE introducing a new personal script to make sure the
agent first checked:
  - Is there an existing personal script doing this? (personal_scripts_list)
  - Is there a reusable skill? (skill_match)
  - Are there learnings saying "don't do X"? (learning_search)

Structural check against recent_tool_records, no LLM.
"""
from __future__ import annotations

from core_prompts import render_core_prompt

INJECTION_PROMPT_TEMPLATE = render_core_prompt(
    "r22-personal-script-injection",
    path="{path}",
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
