"""r19_project_grep — require Grep before Write on project paths with require_grep flag.

Fase 2 Protocol Enforcer Fase D item R19. Plan doc 1 reads:

  SI intent Write sobre path de entidad type=project con flag require_grep
  Y no hubo Grep buscando símbolo/función previamente
  ENTONCES inyectar obligación grep.

Pure decision module — state (projects list, recent_tool_records) is
owned by the caller (HeadlessEnforcer).
"""
from __future__ import annotations


INJECTION_PROMPT_TEMPLATE = (
    "R19 pre-Write on project '{project}' without Grep: the project is "
    "flagged require_grep=true (shared-state hygiene — learning #144) "
    "and the recent tool history has no Grep call covering the function "
    "or constant about to change. BEFORE writing '{path}', run Grep "
    "for the symbol(s) you are introducing / modifying to confirm no "
    "other caller breaks. Do not produce visible text for this "
    "reminder — act on it. After handling the silent action, continue with your previous task; this reminder is silent context, not a new instruction."
)


_WATCHED_WRITE_TOOLS = frozenset({
    "Edit", "Write", "MultiEdit", "NotebookEdit",
    "mcp__nexo__Edit", "mcp__nexo__Write", "mcp__nexo__MultiEdit",
})

_GREP_TOOLS = frozenset({
    "Grep", "mcp__nexo__Grep",
})


def _match_project_for_path(file_path: str, projects) -> dict | None:
    """Return the project entity whose path_patterns include file_path.

    Expected project shape: {"name": str, "aliases": [str], "require_grep": bool,
                             "path_patterns": [str]}. path_patterns are plain
    substrings (not regex); a match is any substring present in file_path.
    """
    if not file_path or not projects:
        return None
    for project in projects:
        if not isinstance(project, dict):
            continue
        if not project.get("require_grep"):
            continue
        patterns = project.get("path_patterns") or []
        for pattern in patterns:
            pattern_str = str(pattern or "").strip()
            if pattern_str and pattern_str in file_path:
                return project
    return None


def _recent_grep_window(recent_tool_records, window_calls: int = 30) -> bool:
    """True iff any recent record (up to window_calls) is a Grep tool.

    Unlike R20 we do not require the grep to match a specific symbol —
    project-level grep discipline asks for SOME recent grep activity on
    the repo, not a perfect symbol match. R20 covers the stricter case.
    """
    if not recent_tool_records:
        return False
    seen = 0
    for record in reversed(recent_tool_records):
        if seen >= window_calls:
            break
        seen += 1
        tool = str(getattr(record, "tool", "") or "")
        if tool in _GREP_TOOLS:
            return True
    return False


def should_inject_r19(
    tool_name: str,
    file_path: str,
    projects,
    recent_tool_records,
) -> dict | None:
    if tool_name not in _WATCHED_WRITE_TOOLS:
        return None
    if not file_path:
        return None
    project = _match_project_for_path(file_path, projects)
    if not project:
        return None
    if _recent_grep_window(recent_tool_records):
        return None
    return {
        "tag": f"r19:{project.get('name')}:{file_path}",
        "project": project.get("name"),
        "path": file_path,
    }


__all__ = [
    "should_inject_r19",
    "INJECTION_PROMPT_TEMPLATE",
]
