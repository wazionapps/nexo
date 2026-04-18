from __future__ import annotations
"""Dynamic plugin loader for NEXO MCP server."""

import importlib
import importlib.util
import os
import signal
import sys
import time

from db import get_db
from fastmcp.tools import Tool

try:
    from tree_hygiene import is_duplicate_artifact_name
except ModuleNotFoundError as exc:
    if getattr(exc, "name", "") != "tree_hygiene":
        raise

    # Keep older runtimes bootable long enough to receive tree_hygiene.py
    # during update; duplicate filtering will resume once the module lands.
    def is_duplicate_artifact_name(_path) -> bool:
        return False

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


# Plan Consolidado R11 — plugin_load pre-inventory check.
# Before loading any plugin, verify it is declared in
# ``tool-enforcement-map.json`` OR explicitly allow-listed below. This
# prevents a stray `.py` file dropped into `src/plugins/` (or the user's
# personal plugins dir) from loading silently and registering tools that
# the Guardian has no entry for. Honours learning #335.
_R11_ALLOW_LIST = frozenset({
    # Names a plugin file can have that we never want to block — purely
    # scaffolding files that ship with every install. Intentionally small.
    "__init__.py",
})


def _collect_declared_plugin_names_from_map() -> set[str]:
    """Parse the repo's tool-enforcement-map.json and return the set of
    plugin-backed tool names. Falls back to empty on any IO / JSON error
    so a broken map never hard-blocks plugin loading (soft gate)."""
    try:
        map_path = os.path.join(os.path.dirname(SERVER_DIR), "tool-enforcement-map.json")
        if not os.path.isfile(map_path):
            return set()
        import json as _json
        with open(map_path, "r", encoding="utf-8") as fh:
            data = _json.load(fh)
        tools = data.get("tools") or {}
        return {
            name for name, meta in tools.items()
            if isinstance(meta, dict) and meta.get("source") in ("plugin", "personal_plugin")
        }
    except Exception:
        return set()


def _collect_declared_plugin_tool_names(plugin_path: str) -> set[str]:
    """Scan a plugin file for ``"nexo_<name>"`` string literals that match
    its tool declarations. Parser-free (regex) so we don't import the
    plugin before deciding to load it (pre-inventory is, by definition,
    pre-import)."""
    try:
        import re as _re
        with open(plugin_path, "r", encoding="utf-8") as fh:
            source = fh.read()
        return set(_re.findall(r'"(nexo_[a-z_]+)"', source))
    except Exception:
        return set()


def verify_plugin_in_inventory(filename: str, plugin_path: str) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for a plugin before it is loaded.

    A plugin passes the R11 gate when EITHER:
      - The filename is in the allow-list (scaffolding files).
      - At least one nexo_<tool> string inside the plugin matches an
        entry whose ``source`` is ``plugin`` / ``personal_plugin`` in
        ``tool-enforcement-map.json``.

    Plugins with no tool strings pass (they may be helper modules);
    only plugins that DECLARE tools but none of those tools are in the
    map are rejected.
    """
    if filename in _R11_ALLOW_LIST:
        return True, "allow-listed"
    declared = _collect_declared_plugin_tool_names(plugin_path)
    if not declared:
        return True, "no tools declared"
    known = _collect_declared_plugin_names_from_map()
    if not known:
        # Map missing or unreadable — soft pass (we don't want a broken
        # map to block every plugin).
        return True, "map unavailable"
    intersection = declared & known
    if not intersection:
        return False, (
            f"plugin tools {sorted(declared)} not present in "
            "tool-enforcement-map.json (add entries or update `source`)."
        )
    return True, f"matched {sorted(intersection)}"


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
                if is_duplicate_artifact_name(os.path.join(PLUGINS_DIR, f)):
                    continue
                plugin_map[f] = (PLUGINS_DIR, "repo")

    # 2. Personal plugins (override if same filename)
    if os.path.isdir(PERSONAL_PLUGINS_DIR):
        for f in sorted(os.listdir(PERSONAL_PLUGINS_DIR)):
            if f.endswith(".py") and f != "__init__.py":
                if is_duplicate_artifact_name(os.path.join(PERSONAL_PLUGINS_DIR, f)):
                    continue
                source = "personal (override)" if f in plugin_map else "personal"
                plugin_map[f] = (PERSONAL_PLUGINS_DIR, source)

    # Load all in sorted order
    for f in sorted(plugin_map):
        plugins_dir, source_label = plugin_map[f]
        # Plan Consolidado R11 — pre-inventory gate. Reject unknown plugins
        # before spawning their SIGALRM timeout + import.
        plugin_path = os.path.join(plugins_dir, f)
        ok, reason = verify_plugin_in_inventory(f, plugin_path)
        if not ok:
            print(f"[R11 REJECT] {f}: {reason}", file=sys.stderr)
            continue
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
        plugins_dir: Directory to load from. If None, searches repo PLUGINS_DIR first,
                     then PERSONAL_PLUGINS_DIR. Personal plugins are loaded via
                     importlib.util.spec_from_file_location.
    """
    if not filename.endswith(".py"):
        filename += ".py"

    # Reject path separators and traversal sequences before joining
    if "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError(f"Invalid plugin filename (path separators or '..' not allowed): {filename}")

    if plugins_dir is not None:
        filepath = os.path.join(plugins_dir, filename)
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"Plugin not found: {filepath}")
    else:
        # Search repo first, then personal
        repo_path = os.path.join(PLUGINS_DIR, filename)
        personal_path = os.path.join(PERSONAL_PLUGINS_DIR, filename)
        if os.path.isfile(repo_path):
            plugins_dir = PLUGINS_DIR
            filepath = repo_path
        elif os.path.isfile(personal_path):
            plugins_dir = PERSONAL_PLUGINS_DIR
            filepath = personal_path
        else:
            raise FileNotFoundError(
                f"Plugin not found in repo ({PLUGINS_DIR}) or personal ({PERSONAL_PLUGINS_DIR}): {filename}"
            )

    # Security: reject path traversal — resolved path must stay inside allowed directories
    real_path = os.path.realpath(filepath)
    real_plugins = os.path.realpath(PLUGINS_DIR)
    real_personal = os.path.realpath(PERSONAL_PLUGINS_DIR)
    if not (real_path.startswith(real_plugins + os.sep) or real_path.startswith(real_personal + os.sep)):
        raise ValueError(
            f"Path traversal blocked: {filename!r} resolves to {real_path}, "
            f"which is outside {real_plugins} and {real_personal}"
        )

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

    source_label = "personal" if plugins_dir != PLUGINS_DIR else "repo"
    _update_registry(filename, len(tool_names), ",".join(tool_names), source_label)

    return len(tool_names)


def remove_plugin(mcp, filename: str) -> list[str]:
    """Unregister a plugin's tools from MCP and clean the registry.

    Does NOT delete plugin files — only unregisters tools to avoid
    accidental deletion of code from repo or personal directories.
    """
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

    conn = get_db()
    conn.execute("DELETE FROM plugins WHERE filename = ?", (filename,))
    conn.commit()

    return removed


def list_plugins() -> list[dict]:
    """List all registered plugins with source info (repo/personal)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT filename, tools_count, tool_names, loaded_at, created_by FROM plugins ORDER BY filename"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["source"] = d.get("created_by", "repo")
        result.append(d)
    return result


def _update_registry(filename: str, tools_count: int, tool_names: str, created_by: str):
    """Insert or update plugin registry entry. Non-fatal on lock — tools still work."""
    now = time.time()
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO plugins (filename, tools_count, tool_names, loaded_at, created_by) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(filename) DO UPDATE SET tools_count=?, tool_names=?, loaded_at=?, created_by=?",
            (filename, tools_count, tool_names, now, created_by, tools_count, tool_names, now, created_by),
        )
        conn.commit()
    except Exception as e:
        print(f"[PLUGIN REGISTRY] Skipped update for {filename}: {e}")
