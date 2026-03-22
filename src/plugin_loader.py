"""Dynamic plugin loader for NEXO MCP server."""

import importlib
import os
import signal
import sys
import time

from db import get_db
from fastmcp.tools import Tool

PLUGINS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")

PLUGIN_LOAD_TIMEOUT = 10  # seconds per plugin


class _PluginTimeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _PluginTimeout("Plugin loading timed out")


def load_all_plugins(mcp) -> int:
    """Load all plugins from plugins/ directory at startup. Returns total tools loaded."""
    if not os.path.isdir(PLUGINS_DIR):
        return 0
    total = 0
    for f in sorted(os.listdir(PLUGINS_DIR)):
        if f.endswith(".py") and f != "__init__.py":
            try:
                old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
                signal.alarm(PLUGIN_LOAD_TIMEOUT)
                try:
                    n = load_plugin(mcp, f)
                    total += n
                finally:
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, old_handler)
            except _PluginTimeout:
                print(f"[PLUGIN TIMEOUT] {f}: skipped after {PLUGIN_LOAD_TIMEOUT}s", file=sys.stderr)
            except Exception as e:
                print(f"[PLUGIN ERROR] {f}: {e}", file=sys.stderr)
    return total


def load_plugin(mcp, filename: str) -> int:
    """Load or reload a single plugin. Returns number of tools registered."""
    if not filename.endswith(".py"):
        filename += ".py"

    filepath = os.path.join(PLUGINS_DIR, filename)
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"Plugin not found: {filepath}")

    module_name = f"plugins.{filename[:-3]}"

    if module_name in sys.modules:
        mod = importlib.reload(sys.modules[module_name])
    else:
        mod = importlib.import_module(module_name)

    tools_list = getattr(mod, "TOOLS", [])
    tool_names = []

    for func, name, description in tools_list:
        try:
            mcp.local_provider.remove_tool(name)
        except Exception:
            pass
        t = Tool.from_function(func, name=name, description=description)
        mcp.add_tool(t)
        tool_names.append(name)

    _update_registry(filename, len(tool_names), ",".join(tool_names), "manual")

    return len(tool_names)


def remove_plugin(mcp, filename: str) -> list[str]:
    """Remove a plugin: unregister its tools, delete file, clean registry."""
    if not filename.endswith(".py"):
        filename += ".py"

    conn = get_db()
    row = conn.execute("SELECT tool_names FROM plugins WHERE filename = ?", (filename,)).fetchone()

    removed = []
    if row and row["tool_names"]:
        for name in row["tool_names"].split(","):
            name = name.strip()
            if name:
                try:
                    mcp.local_provider.remove_tool(name)
                    removed.append(name)
                except Exception:
                    pass

    module_name = f"plugins.{filename[:-3]}"
    sys.modules.pop(module_name, None)

    filepath = os.path.join(PLUGINS_DIR, filename)
    if os.path.isfile(filepath):
        os.remove(filepath)

    conn = get_db()
    conn.execute("DELETE FROM plugins WHERE filename = ?", (filename,))
    conn.commit()

    return removed


def list_plugins() -> list[dict]:
    """List all registered plugins."""
    conn = get_db()
    rows = conn.execute(
        "SELECT filename, tools_count, tool_names, loaded_at, created_by FROM plugins ORDER BY filename"
    ).fetchall()
    return [dict(r) for r in rows]


def _update_registry(filename: str, tools_count: int, tool_names: str, created_by: str):
    """Insert or update plugin registry entry. Non-fatal on lock — tools still work."""
    now = time.time()
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO plugins (filename, tools_count, tool_names, loaded_at, created_by) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(filename) DO UPDATE SET tools_count=?, tool_names=?, loaded_at=?",
            (filename, tools_count, tool_names, now, created_by, tools_count, tool_names, now),
        )
        conn.commit()
    except Exception as e:
        print(f"[PLUGIN REGISTRY] Skipped update for {filename}: {e}")
