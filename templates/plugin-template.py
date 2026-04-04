"""Personal NEXO MCP plugin scaffold.

This file lives in NEXO_HOME/plugins/ and is loaded by the NEXO MCP server.
Edit the handler below to implement your personal capability.
"""

from __future__ import annotations

import json


def handle_example_tool(payload_json: str = "{}") -> str:
    """Example personal MCP tool.

    Args:
        payload_json: JSON string with your input payload.
    """
    try:
        payload = json.loads(payload_json or "{}")
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"invalid json: {exc}"}, ensure_ascii=False)

    return json.dumps({
        "ok": True,
        "message": "Personal plugin scaffold created. Edit this handler in NEXO_HOME/plugins.",
        "payload": payload,
    }, ensure_ascii=False)


TOOLS = [
    (
        handle_example_tool,
        "nexo_example_tool",
        "Example personal MCP tool scaffold. Edit it in NEXO_HOME/plugins.",
    ),
]
