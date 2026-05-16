from __future__ import annotations

"""Required MCP tool contract shared by Brain and Desktop probes.

These tools are the minimum bootstrap surface NEXO Desktop needs before a
conversation can be considered healthy. Dynamic plugin loading may still add
more tools, but these names must always be present.
"""

BOOTSTRAP_REQUIRED_MCP_TOOLS: tuple[str, ...] = (
    "nexo_startup",
    "nexo_heartbeat",
    "nexo_session_diary_read",
    "nexo_reminders",
    "nexo_smart_startup",
    "nexo_task_open",
    "nexo_task_close",
    "nexo_task_acknowledge_guard",
    "nexo_guard_check",
    "nexo_learning_add",
    "nexo_confidence_check",
    "nexo_followup_create",
    "nexo_protocol_debt_resolve",
    "nexo_card_match",
    "nexo_skill_match",
)


def missing_required_tools(tool_names: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    available = {str(name) for name in tool_names}
    return [name for name in BOOTSTRAP_REQUIRED_MCP_TOOLS if name not in available]
