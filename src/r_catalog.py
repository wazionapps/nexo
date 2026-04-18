"""R-CATALOG — Plan Consolidado 0.X.2.

Pre-create discovery probe: when the agent is about to create a resource
(``nexo_*_create`` family) without having consulted any inventory tool in
the same turn, R-CATALOG nudges it to search first.

Rationale (Plan Consolidado §FASE 0.X): the Guardian should not re-teach
what tools exist; the live catalog does that. But if the agent never
reads the catalog before creating a new artefact, it skips discovery and
produces duplicates (already-existing skills cloned as personal scripts,
learnings with equivalent content, followups on resolved work, etc.).

Contract:
  - Trigger tools: any tool matching ``nexo_*_create`` / ``_open`` / ``_add``.
  - Discovery tools (any one resets the window): ``nexo_system_catalog``,
    ``nexo_tool_explain``, ``nexo_skill_match``, ``nexo_skill_list``,
    ``nexo_learning_search``, ``nexo_guard_check``.
  - Window: 60s. Caller passes ``recent_tool_names`` already filtered to
    the last 60 seconds so the rule itself is time-agnostic.
  - Dedup tag: the engine's _enqueue keys dedup by rule_id + tag so an
    agent chaining two creates only gets nudged once per 60s.
"""
from __future__ import annotations

from typing import Iterable


DISCOVERY_TOOLS = frozenset({
    "nexo_system_catalog",
    "nexo_tool_explain",
    "nexo_skill_match",
    "nexo_skill_list",
    "nexo_learning_search",
    "nexo_guard_check",
})

INJECTION_PROMPT = (
    "R-CATALOG pre-create probe: about to call {tool} without having "
    "consulted the live inventory in this turn. Run one of "
    "`nexo_system_catalog(query=...)`, `nexo_skill_match`, "
    "`nexo_tool_explain`, `nexo_learning_search` or `nexo_guard_check` "
    "first to avoid duplicating an existing artefact."
)


def _is_trigger_tool(tool_name) -> bool:
    if not isinstance(tool_name, str) or not tool_name.startswith("nexo_"):
        return False
    return tool_name.endswith("_create") or tool_name.endswith("_open") or tool_name.endswith("_add")


def should_inject_r_catalog(
    tool_name,
    *,
    recent_tool_names: Iterable[str] | None,
) -> tuple[bool, str]:
    """Return (inject, prompt). Never raises."""
    if not _is_trigger_tool(tool_name):
        return False, ""
    if tool_name in DISCOVERY_TOOLS:
        return False, ""
    recent = set(recent_tool_names or [])
    if recent & DISCOVERY_TOOLS:
        return False, ""
    return True, INJECTION_PROMPT.format(tool=tool_name)


__all__ = [
    "DISCOVERY_TOOLS",
    "INJECTION_PROMPT",
    "should_inject_r_catalog",
]
