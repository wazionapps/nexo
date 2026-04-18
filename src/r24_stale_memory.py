"""r24_stale_memory — warn when a stale memory is cited without verification.

Fase 2 Protocol Enforcer Fase D item R24. Plan doc 1 reads:

  SI en los últimos 3 turnos se citó dato de recall/memory con age>7
  Y no hubo tool call de verificación fresh
  ENTONCES warning.

Implementation approach:

  - The caller notifies the enforcer when a memory older than N days
    was referenced (e.g. after handle_cognitive_retrieve, the
    annotated age_days from R07 indicates >= 7). Mark the session as
    "staleness-aware" with a small decay counter.
  - On subsequent tool calls, we check whether a "fresh verification"
    tool fired (Grep, Bash, Read for the relevant target, etc). If
    the decay counter expires without any verification, R24 warns.

R24 is a SHADOW-by-default rule per plan doc 1. This module returns
the decision primitives; the caller owns the state.
"""
from __future__ import annotations


INJECTION_PROMPT_TEMPLATE = (
    "R24 stale memory use: your recent context included memory entries "
    "older than {threshold_days} days, and no fresh verification tool "
    "(Grep / Bash / Read of the target) has been called in the meantime. "
    "Before acting on the stale memory, re-verify: search the live repo "
    "/ runtime state / database for the fact the memory asserts. "
    "Learning #156 / #213 — past confabulations traced to acting on stale "
    "memory without checking current state. Do not produce visible text "
    "for this reminder — act on it. After handling the silent action, continue with your previous task; this reminder is silent context, not a new instruction."
)


_VERIFICATION_TOOLS = frozenset({
    "Grep", "mcp__nexo__Grep",
    "Read", "mcp__nexo__Read",
    "Bash", "mcp__nexo__Bash",
    "nexo_system_catalog", "mcp__nexo__nexo_system_catalog",
    "nexo_tool_explain", "mcp__nexo__nexo_tool_explain",
    "nexo_entity_list", "mcp__nexo__nexo_entity_list",
    "nexo_artifact_find", "mcp__nexo__nexo_artifact_find",
})


DEFAULT_STALE_THRESHOLD_DAYS = 7
DEFAULT_WINDOW_TOOL_CALLS = 3


def is_verification_tool(tool_name: str) -> bool:
    return tool_name in _VERIFICATION_TOOLS


def should_flag_r24(
    stale_memory_seen: bool,
    verification_seen_since: bool,
    window_exhausted: bool,
) -> bool:
    """Return True when R24 should fire.

    Logic deliberately kept boolean + explicit so the caller can assemble
    window / decay / cross-turn accounting however it likes.
    """
    return bool(stale_memory_seen) and not verification_seen_since and bool(window_exhausted)


__all__ = [
    "is_verification_tool",
    "should_flag_r24",
    "INJECTION_PROMPT_TEMPLATE",
    "DEFAULT_STALE_THRESHOLD_DAYS",
    "DEFAULT_WINDOW_TOOL_CALLS",
]
