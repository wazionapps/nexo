"""NEXO Personal Plugins — scaffold persistent MCP tools in NEXO_HOME/plugins."""

from __future__ import annotations

import json
import os
from pathlib import Path

from db import init_db
from script_registry import create_script


NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[1])))


def _plugins_dir() -> Path:
    path = NEXO_HOME / "plugins"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_slug(value: str) -> str:
    chars: list[str] = []
    for ch in str(value or "").lower():
        if ch.isalnum():
            chars.append(ch)
        elif ch in {"-", "_", " "}:
            chars.append("-")
    slug = "".join(chars).strip("-")
    return slug or "plugin"


def _template_path(name: str) -> Path | None:
    candidates = [
        NEXO_CODE.parent / "templates" / name,
        NEXO_HOME / "templates" / name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _load_template() -> str:
    template = _template_path("plugin-template.py")
    if template:
        return template.read_text()
    return (
        "from __future__ import annotations\n"
        "import json\n\n"
        "def handle_example_tool(payload_json: str = \"{}\") -> str:\n"
        "    try:\n"
        "        payload = json.loads(payload_json or \"{}\")\n"
        "    except Exception as exc:\n"
        "        return json.dumps({\"ok\": False, \"error\": f\"invalid json: {exc}\"}, ensure_ascii=False)\n"
        "    return json.dumps({\"ok\": True, \"payload\": payload}, ensure_ascii=False)\n\n"
        "TOOLS = [\n"
        "    (handle_example_tool, \"nexo_example_tool\", \"Example personal MCP tool scaffold.\"),\n"
        "]\n"
    )


def _render_plugin_template(*, plugin_stem: str, tool_name: str, description: str) -> str:
    content = _load_template()
    handler_name = f"handle_{plugin_stem.replace('-', '_')}"
    content = content.replace("handle_example_tool", handler_name)
    content = content.replace("nexo_example_tool", tool_name)
    content = content.replace(
        "Personal plugin scaffold created. Edit this handler in NEXO_HOME/plugins.",
        description or f"Personal plugin scaffold for {plugin_stem}.",
    )
    content = content.replace(
        "Example personal MCP tool scaffold. Edit it in NEXO_HOME/plugins.",
        description or f"Personal MCP tool scaffold for {plugin_stem}.",
    )
    return content


def handle_personal_plugin_create(
    name: str,
    description: str = "",
    tool_name: str = "",
    create_companion_script: bool = False,
    script_runtime: str = "python",
    force: bool = False,
) -> str:
    """Create a personal MCP plugin scaffold in NEXO_HOME/plugins.

    Optionally also creates a companion script in NEXO_HOME/scripts.
    """
    init_db()
    plugin_stem = _safe_slug(name)
    filename = f"{plugin_stem}.py"
    tool_name = (tool_name or f"nexo_{plugin_stem.replace('-', '_')}").strip()
    plugin_path = _plugins_dir() / filename
    if plugin_path.exists() and not force:
        return json.dumps({
            "ok": False,
            "error": f"Plugin already exists: {plugin_path}",
        }, ensure_ascii=False)

    content = _render_plugin_template(
        plugin_stem=plugin_stem,
        tool_name=tool_name,
        description=description or f"Personal MCP tool for {name}.",
    )
    plugin_path.write_text(content)

    script_result = None
    if create_companion_script:
        script_result = create_script(
            plugin_stem,
            description=f"Companion script for plugin {plugin_stem}",
            runtime=script_runtime,
            force=force,
        )

    return json.dumps({
        "ok": True,
        "name": plugin_stem,
        "tool_name": tool_name,
        "plugin_path": str(plugin_path),
        "companion_script": script_result,
        "next_step": f"Load the plugin with nexo_plugin_load(filename='{filename}') after editing it.",
    }, ensure_ascii=False)


TOOLS = [
    (
        handle_personal_plugin_create,
        "nexo_personal_plugin_create",
        "Create a persistent personal MCP plugin scaffold in NEXO_HOME/plugins, optionally with a companion script in NEXO_HOME/scripts.",
    ),
]
