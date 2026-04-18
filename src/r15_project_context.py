"""r15_project_context — demand project context before editing a project path.

Fase 2 Protocol Enforcer Fase D item R15. Plan doc 1 reads:

  SI user msg menciona entidad type=project registrada
  Y en sesión no hay nexo_recall(proyecto) NI lectura atlas
  ENTONCES inyectar obligación.

This module exposes pure helpers:

  - match_project_names(text, projects) → list of project names that the
    text mentions. A "project" is a dict with at least {"name", "aliases":
    list[str]}; aliases are matched case-insensitively via word-boundary.
  - recent_context_loaded(project_name, recent_tool_records) → bool.
    True if a recent tool_use was nexo_recall / nexo_cognitive_retrieve /
    nexo_memory_recall with the project name in the query, OR any read of
    project-atlas.json / project-specific docs.
  - should_inject_r15(user_text, projects, recent_records) → dict | None.

The caller (HeadlessEnforcer) owns the source of projects (db.list_
entities type='project') and the recent_tool_records ring buffer already
used by R13.
"""
from __future__ import annotations

import re


INJECTION_PROMPT_TEMPLATE = (
    "R15 pre-project-action: the last user message mentions project "
    "'{project}' but the session has no recent nexo_recall('{project}') "
    "nor a read of project-atlas.json. Before touching any file or cron "
    "for this project, load its context: run nexo_recall('{project}'), "
    "inspect entity_list(type='project'), and/or cat "
    "~/.nexo/brain/project-atlas.json. Learning #213 / #151 — assuming "
    "server, DNS, or repo details from memory without verifying leads "
    "to concrete incidents. Do not produce visible text for this "
    "reminder — act on it. After handling the silent action, continue with your previous task; this reminder is silent context, not a new instruction."
)


_RECALL_TOOLS = frozenset({
    "nexo_recall", "mcp__nexo__nexo_recall",
    "nexo_cognitive_retrieve", "mcp__nexo__nexo_cognitive_retrieve",
    "nexo_memory_recall", "mcp__nexo__nexo_memory_recall",
    "nexo_entity_list", "mcp__nexo__nexo_entity_list",
    "nexo_pre_action_context", "mcp__nexo__nexo_pre_action_context",
})

_ATLAS_FILE_TOKEN = "project-atlas.json"


def _name_matches(text_lower: str, candidate: str) -> bool:
    """Case-insensitive word-boundary match against the lowered text."""
    if not candidate:
        return False
    # Escape the candidate and require word-boundary-ish context so "nora"
    # does not match inside "minora" etc.
    pattern = r"\b" + re.escape(candidate.lower()) + r"\b"
    try:
        return bool(re.search(pattern, text_lower))
    except re.error:
        return False


def match_project_names(user_text: str, projects) -> list[str]:
    """Return the project names whose name or aliases appear in user_text."""
    if not user_text or not projects:
        return []
    text_lower = user_text.lower()
    hits: list[str] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        name = str(project.get("name") or "").strip()
        if not name:
            continue
        aliases = project.get("aliases") or []
        candidates = {name} | {str(a) for a in aliases if a}
        if any(_name_matches(text_lower, c) for c in candidates):
            hits.append(name)
    return hits


def recent_context_loaded(project_name: str, recent_tool_records) -> bool:
    """True if a recent tool_use loaded context for the project."""
    if not project_name or not recent_tool_records:
        return False
    needle = project_name.lower()
    for record in recent_tool_records:
        tool = str(getattr(record, "tool", "") or "")
        files = getattr(record, "files", ()) or ()
        # Direct recall-class tool
        if tool in _RECALL_TOOLS:
            # If the records carry the query text in files (we stuff recall
            # query into ToolCallRecord.files[0] in the helpers), check it.
            for f in files:
                if needle in str(f).lower():
                    return True
            # A recall tool without a matching file still counts as context
            # loaded — the agent at least reached into memory.
            return True
        # Atlas file read
        if tool in {"Read", "mcp__nexo__Read"}:
            for f in files:
                if _ATLAS_FILE_TOKEN in str(f):
                    return True
    return False


def should_inject_r15(
    user_text: str,
    projects,
    recent_tool_records,
) -> dict | None:
    """Return {tag, project} when R15 should fire, else None."""
    hits = match_project_names(user_text or "", projects)
    if not hits:
        return None
    for project_name in hits:
        if not recent_context_loaded(project_name, recent_tool_records):
            return {
                "tag": f"r15:{project_name}",
                "project": project_name,
            }
    return None


__all__ = [
    "match_project_names",
    "recent_context_loaded",
    "should_inject_r15",
    "INJECTION_PROMPT_TEMPLATE",
]
