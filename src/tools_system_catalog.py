"""Public MCP tools for the live NEXO system catalog / ontology."""

from __future__ import annotations

from system_catalog import build_system_catalog, explain_tool, format_catalog, format_tool_explanation


def handle_system_catalog(section: str = "", query: str = "", limit: int = 20) -> str:
    catalog = build_system_catalog()
    return format_catalog(
        catalog,
        section=(section or "").strip(),
        query=(query or "").strip(),
        limit=max(1, int(limit or 20)),
    )


def handle_tool_explain(name: str) -> str:
    return format_tool_explanation(explain_tool(name))
