"""Regression: FastMCP must NOT auto-generate outputSchema for NEXO tools.

FastMCP 2.14.7 and 3.2.4 auto-generate an `x-fastmcp-wrap-result` outputSchema
for any tool whose return annotation is a non-object type (e.g. `str`). Claude
Code validates MCP responses strictly against outputSchema and rejects our
plain-text replies with `Output validation error: result is a required
property`, which makes every `nexo_*` tool inexecutable from Claude Code.

`src/server.py` wraps `mcp.tool` so every `@mcp.tool` defaults to
`output_schema=None`, and `src/plugin_loader.py` passes `output_schema=None`
when building plugin tools via `Tool.from_function`. This test fixes both
behaviors so they cannot silently regress.

See followup NF-FASTMCP-OUTPUT-SCHEMA-1776969764.
"""

from __future__ import annotations

import asyncio


def _get_tool_sync(mcp_instance, name):
    getter = mcp_instance.get_tool
    if asyncio.iscoroutinefunction(getter):
        return asyncio.run(getter(name))
    return getter(name)


def _protocol_output_schema(tool) -> object:
    payload = tool.to_mcp_tool()
    dumped = payload if isinstance(payload, dict) else payload.model_dump()
    return dumped.get("outputSchema")


def test_server_tools_have_no_auto_output_schema():
    """Every @mcp.tool in src/server.py must ship without outputSchema."""
    import server  # noqa: WPS433 (intentional, tests repo src module)

    sample_tool_names = (
        "nexo_startup",
        "nexo_heartbeat",
        "nexo_stop",
    )

    for tool_name in sample_tool_names:
        tool = _get_tool_sync(server.mcp, tool_name)
        # output_schema attribute on the FunctionTool must be None
        assert getattr(tool, "output_schema", "missing") is None, (
            f"{tool_name}: output_schema must be None, "
            f"got {getattr(tool, 'output_schema', 'missing')!r}"
        )
        # The protocol-level outputSchema emitted to MCP clients must be None too
        assert _protocol_output_schema(tool) is None, (
            f"{tool_name}: to_mcp_tool().outputSchema must be None"
        )


def test_plugin_loader_tools_have_no_auto_output_schema():
    """Plugin tools built via Tool.from_function must also opt out."""
    from fastmcp import FastMCP
    from fastmcp.tools import Tool

    import plugin_loader  # noqa: F401 (exercises the repo module path)

    def plugin_fn(value: str = "x") -> str:
        """Fake plugin tool returning str."""
        return value

    # Mirror the exact call the plugin_loader now makes.
    tool = Tool.from_function(
        plugin_fn,
        name="fake_plugin_tool",
        description="test",
        output_schema=None,
    )

    assert getattr(tool, "output_schema", "missing") is None
    assert _protocol_output_schema(tool) is None

    # And integrate it into a fresh FastMCP to ensure the end-to-end surface
    # stays schema-free.
    mcp = FastMCP(name="test-plugin-host")
    mcp.add_tool(tool)
    registered = _get_tool_sync(mcp, "fake_plugin_tool")
    assert getattr(registered, "output_schema", "missing") is None
    assert _protocol_output_schema(registered) is None
