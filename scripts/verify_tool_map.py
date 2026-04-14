#!/usr/bin/env python3
"""Verify tool-enforcement-map.json is in sync with actual tool definitions.

Scans server.py + all plugin files for nexo_ tool definitions and compares
against the keys in tool-enforcement-map.json. Exits non-zero if:
  - A tool exists in code but not in the map (missing)
  - A tool exists in the map but not in code (orphaned)

Run: python3 scripts/verify_tool_map.py
"""

import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join(REPO_ROOT, "tool-enforcement-map.json")
SRC_DIR = os.path.join(REPO_ROOT, "src")
SERVER_PY = os.path.join(SRC_DIR, "server.py")
PLUGINS_DIR = os.path.join(SRC_DIR, "plugins")

IGNORE_TOOLS = {"nexo_example_tool"}


def get_server_tools() -> set[str]:
    with open(SERVER_PY) as f:
        content = f.read()
    return {m.group(1) for m in re.finditer(r"def (nexo_[a-z_]+)\(", content)}


def get_plugin_tools() -> set[str]:
    tools = set()
    for fname in sorted(os.listdir(PLUGINS_DIR)):
        if not fname.endswith(".py") or fname == "__init__.py":
            continue
        with open(os.path.join(PLUGINS_DIR, fname)) as f:
            content = f.read()
        tools.update(m.group(1) for m in re.finditer(r'"(nexo_[a-z_]+)"', content))
    return tools


def get_map_tools() -> set[str]:
    with open(MAP_PATH) as f:
        data = json.load(f)
    return set(data["tools"].keys())


def main():
    if not os.path.exists(MAP_PATH):
        print(f"ERROR: {MAP_PATH} not found")
        sys.exit(1)

    code_tools = (get_server_tools() | get_plugin_tools()) - IGNORE_TOOLS
    map_tools = get_map_tools()

    missing = code_tools - map_tools
    orphaned = map_tools - code_tools

    ok = True

    if missing:
        ok = False
        print(f"\n  MISSING from map ({len(missing)} tools in code but not in tool-enforcement-map.json):")
        for t in sorted(missing):
            print(f"    - {t}")

    if orphaned:
        ok = False
        print(f"\n  ORPHANED in map ({len(orphaned)} tools in map but not in code):")
        for t in sorted(orphaned):
            print(f"    - {t}")

    if ok:
        print(f"  tool-enforcement-map.json is in sync with code ({len(map_tools)} tools)")
    else:
        print(f"\n  Map has {len(map_tools)} tools, code has {len(code_tools)} tools")
        print("  Run this after adding/removing tools to keep the map up to date.")
        sys.exit(1)


if __name__ == "__main__":
    main()
