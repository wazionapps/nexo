"""R_PRIMITIVE_CHOICE — v7.7 Gap 4.

Before materialising a NEW artefact (skill / plugin / personal script /
template) via plain Edit / Write, the agent MUST consult SK-CREATE-
NEXO-PRIMITIVE (or its equivalent signal — a recent `nexo_skill_match`
/ `nexo_tool_explain` call) so skill-vs-plugin-vs-script-vs-schedule-
vs-core-change is not improvised.

Distinct from R_CATALOG (v7.7 Gap 3) which fires on EVERY write into
an artefact-bearing path; R_PRIMITIVE_CHOICE fires only when the file
is NEW (write without prior matching read/grep) so editing an existing
skill to tweak a step is not reprimanded.

Contract:
  - Trigger: Edit/Write into artefact-bearing paths AND the file did
    NOT appear in the recent tool record as a previous Read/Grep.
  - Relief signal (any one in last 120s): `nexo_skill_match`,
    `nexo_tool_explain`, Read or Grep of the same path, or
    `nexo_skill_apply` referencing SK-CREATE-NEXO-PRIMITIVE.
  - Default mode: soft (so the rule can be observed before hardening).
"""
from __future__ import annotations

from typing import Iterable

from core_prompts import render_core_prompt


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
)

RELIEF_TOOLS = frozenset({
    "nexo_skill_match",
    "nexo_tool_explain",
    "nexo_skill_apply",
    "Read",
    "Grep",
})


INJECTION_PROMPT = render_core_prompt("r-primitive-choice", path="{path}")


def _is_artefact_path(path: str) -> bool:
    if not isinstance(path, str):
        return False
    return any(fragment in path for fragment in ARTEFACT_PATH_FRAGMENTS)


def _file_previously_touched(path: str, recent_records) -> bool:
    """True iff the same path was seen in a prior Read / Grep / Edit
    call inside the recent window. The engine passes its
    recent_tool_records list (each record has .tool and .files)."""
    if not isinstance(path, str):
        return False
    for record in recent_records or []:
        tool = getattr(record, "tool", "")
        if tool not in ("Read", "Grep", "Edit"):
            continue
        files = getattr(record, "files", ())
        for f in files or ():
            if f == path:
                return True
    return False


def should_inject_r_primitive(
    tool_name,
    *,
    files: Iterable[str] | None,
    recent_tool_names: Iterable[str] | None,
    recent_tool_records,
) -> tuple[bool, str]:
    """Return (inject, prompt). Fail-closed on bad input → (False, "")."""
    if tool_name not in ("Edit", "Write"):
        return False, ""
    file_list = [f for f in (files or []) if isinstance(f, str)]
    if not file_list:
        return False, ""
    artefact_files = [f for f in file_list if _is_artefact_path(f)]
    if not artefact_files:
        return False, ""
    # If any relief tool fired recently, rule is satisfied.
    recent = set(recent_tool_names or [])
    if recent & RELIEF_TOOLS:
        return False, ""
    # If the file appears to be an edit of an existing artefact (Read
    # or Grep already touched the same path), treat as relief too —
    # we only want to flag NEW artefact creation.
    for f in artefact_files:
        if _file_previously_touched(f, recent_tool_records):
            return False, ""
    return True, INJECTION_PROMPT.format(path=artefact_files[0])


__all__ = [
    "ARTEFACT_PATH_FRAGMENTS",
    "RELIEF_TOOLS",
    "INJECTION_PROMPT",
    "should_inject_r_primitive",
]
