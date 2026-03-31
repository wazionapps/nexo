"""Dynamic plugin loader for NEXO MCP server."""

import importlib
import importlib.util
import os
import signal
import sys
import time

from db import get_db
from fastmcp.tools import Tool

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR = os.path.join(SERVER_DIR, "plugins")

# Personal plugins directory: NEXO_HOME/plugins/ (env var, defaults to ~/.nexo/)
NEXO_HOME = os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))
PERSONAL_PLUGINS_DIR = os.path.join(NEXO_HOME, "plugins")

PLUGIN_LOAD_TIMEOUT = 10  # seconds per plugin


class _PluginTimeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _PluginTimeout("Plugin loading timed out")


def _ensure_src_in_path():
    """Ensure server src/ is in sys.path so personal plugins can import db, cognitive, etc."""
    if SERVER_DIR not in sys.path:
        sys.path.insert(0, SERVER_DIR)


def load_all_plugins(mcp) -> int:
    """Load all plugins from repo and personal directories at startup. Returns total tools loaded."""
    _ensure_src_in_path()
    total = 0

    # Collect plugins: repo first, personal overrides
    plugin_map = {}  # filename -> (dir_path, source_label)

    # 1. Repo plugins (base)
    if os.path.isdir(PLUGINS_DIR):
        for f in sorted(os.listdir(PLUGINS_DIR)):
            if f.endswith(".py") and f != "__init__.py":
                plugin_map[f] = (PLUGINS_DIR, "repo")

    # 2. Personal plugins (override if same filename)
    if os.path.isdir(PERSONAL_PLUGINS_DIR):
        for f in sorted(os.listdir(PERSONAL_PLUGINS_DIR)):
            if f.endswith(".py") and f != "__init__.py":
                source = "personal (override)" if f in plugin_map else "personal"
                plugin_map[f] = (PERSONAL_PLUGINS_DIR, source)

    # Load all in sorted order
    for f in sorted(plugin_map):
        plugins_dir, source_label = plugin_map[f]
        try:
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(PLUGIN_LOAD_TIMEOUT)
            try:
                n = load_plugin(mcp, f, plugins_dir=plugins_dir)
                total += n
                print(f"[PLUGIN LOADED] {f} ({n} tools) from {source_label}: {plugins_dir}", file=sys.stderr)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
        except _PluginTimeout:
            print(f"[PLUGIN TIMEOUT] {f}: skipped after {PLUGIN_LOAD_TIMEOUT}s", file=sys.stderr)
        except Exception as e:
            print(f"[PLUGIN ERROR] {f}: {e}", file=sys.stderr)
    return total


def load_plugin(mcp, filename: str, plugins_dir: str | None = None) -> int:
    """Load or reload a single plugin. Returns number of tools registered.

    Args:
        plugins_dir: Directory to load from. Defaults to repo PLUGINS_DIR.
                     Personal plugins are loaded via importlib.util.spec_from_file_location.
    """
    if not filename.endswith(".py"):
        filename += ".py"

    if plugins_dir is None:
        plugins_dir = PLUGINS_DIR

    filepath = os.path.join(plugins_dir, filename)
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"Plugin not found: {filepath}")

    module_name = f"plugins.{filename[:-3]}"

    # For personal plugins (outside repo), use spec_from_file_location
    if plugins_dir != PLUGINS_DIR:
        _ensure_src_in_path()
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for {filepath}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
    elif module_name in sys.modules:
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
