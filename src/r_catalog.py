"""R-CATALOG — Plan Consolidado 0.X.2 + v7.7 Gap 3 expansion.

Pre-create discovery probe. Two trigger families:

  (a) `nexo_*_create` / `_open` / `_add` — the original MCP-tool path.
  (b) v7.7: `Edit` / `Write` writing into artefact-bearing paths
      (skills/, plugins/, scripts/, personal scripts). The checklist
      item "ampliar el ámbito del pre-probe de catálogo para cubrir
      no solo nexo_*_create/_open/_add, sino también writes de
      archivos que materializan skills/plugins/scripts/plantillas/
      artefactos aunque no hayan pasado por un tool MCP de 'create'".

Rationale: the Guardian should not re-teach what tools exist; the live
catalog does that. But if the agent materialises a new skill / plugin /
script by writing a file directly, without consulting inventory, it
skips discovery and produces duplicates.

Contract:
  - Trigger tools: `nexo_*_create` / `_open` / `_add` (always), or
    `Edit` / `Write` when the file path lives under a recognised
    artefact root.
  - Discovery tools (any one resets the window): `nexo_system_catalog`,
    `nexo_tool_explain`, `nexo_skill_match`, `nexo_skill_list`,
    `nexo_learning_search`, `nexo_guard_check`, `nexo_personal_scripts_list`,
    `nexo_plugin_list`.
  - Window: 60s. Caller passes `recent_tool_names` already filtered to
    the last 60 seconds so the rule itself is time-agnostic.
"""
from __future__ import annotations

from typing import Iterable

from core_prompts import render_core_prompt


DISCOVERY_TOOLS = frozenset({
    "nexo_system_catalog",
    "nexo_tool_explain",
    "nexo_skill_match",
    "nexo_skill_list",
    "nexo_learning_search",
    "nexo_guard_check",
    # v7.7 Gap 3: inventory surfaces that count as "I checked what
    # already exists before writing a new artefact".
    "nexo_personal_scripts_list",
    "nexo_plugin_list",
})

# Path fragments that classify a write as an "artefact creation" write,
# even when it goes through plain Edit / Write instead of a dedicated
# MCP tool. v7.7 Gap 3 coverage.
ARTEFACT_PATH_FRAGMENTS = (
    "/skills/",
    "/clawhub-skill/",
    "/.claude-plugin/",
    "/src/plugins/",
    "/personal/scripts/",
    "/personal/skills/",
    "/personal-scripts/",
    "/.nexo/personal/scripts/",
    "/.nexo/skills/",
    "/templates/core-prompts/",
    "/core-prompts/",
    "/src/presets/",
)

INJECTION_PROMPT = render_core_prompt("r-catalog", tool="{tool}")


def _is_trigger_tool(tool_name) -> bool:
    if not isinstance(tool_name, str) or not tool_name.startswith("nexo_"):
        return False
    return tool_name.endswith("_create") or tool_name.endswith("_open") or tool_name.endswith("_add")


def _is_artefact_write(tool_name, files: Iterable[str] | None) -> bool:
    """v7.7 Gap 3: a plain Edit/Write into an artefact-bearing path
    counts as a trigger even without a *_create MCP tool."""
    if tool_name not in ("Edit", "Write"):
        return False
    if not files:
        return False
    for path in files:
        if not isinstance(path, str):
            continue
        for fragment in ARTEFACT_PATH_FRAGMENTS:
            if fragment in path:
                return True
    return False


def should_inject_r_catalog(
    tool_name,
    *,
    recent_tool_names: Iterable[str] | None,
    files: Iterable[str] | None = None,
) -> tuple[bool, str]:
    """Return (inject, prompt). Never raises."""
    if not _is_trigger_tool(tool_name) and not _is_artefact_write(tool_name, files):
        return False, ""
    if tool_name in DISCOVERY_TOOLS:
        return False, ""
    recent = set(recent_tool_names or [])
    if recent & DISCOVERY_TOOLS:
        return False, ""
    return True, INJECTION_PROMPT.format(tool=tool_name)


__all__ = [
    "DISCOVERY_TOOLS",
    "ARTEFACT_PATH_FRAGMENTS",
    "INJECTION_PROMPT",
    "should_inject_r_catalog",
]
