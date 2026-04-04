#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_JSON = ROOT / "package.json"
PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
MCP_JSON = ROOT / ".mcp.json"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
SERVER_PY = ROOT / "src" / "server.py"


def fail(message: str) -> None:
    raise SystemExit(f"[claude-plugin] {message}")


def main() -> None:
    package = json.loads(PACKAGE_JSON.read_text())
    plugin = json.loads(PLUGIN_JSON.read_text())
    mcp = json.loads(MCP_JSON.read_text())

    if plugin.get("name") != package.get("name"):
        fail("plugin.json name does not match package.json")
    if plugin.get("version") != package.get("version"):
        fail("plugin.json version does not match package.json")
    if plugin.get("mcpServers") != "./.mcp.json":
        fail("plugin.json must reference ./.mcp.json")
    if plugin.get("hooks") != "./hooks/hooks.json":
        fail("plugin.json must reference ./hooks/hooks.json")

    files = package.get("files") or []
    required_entries = {".claude-plugin/", ".mcp.json", "hooks/hooks.json", "src/"}
    if not required_entries.issubset(set(files)):
        fail("package.json files is missing plugin packaging entries")

    servers = mcp.get("mcpServers") or {}
    nexo = servers.get("nexo")
    if not isinstance(nexo, dict):
        fail(".mcp.json is missing mcpServers.nexo")
    if nexo.get("command") != "${CLAUDE_PLUGIN_DATA}/.venv/bin/python3":
        fail("unexpected Claude plugin python command")
    args = nexo.get("args") or []
    if args != ["${CLAUDE_PLUGIN_ROOT}/src/server.py"]:
        fail("unexpected Claude plugin args")
    env = nexo.get("env") or {}
    if env.get("NEXO_HOME") != "${CLAUDE_PLUGIN_DATA}":
        fail("unexpected Claude plugin NEXO_HOME")
    if env.get("NEXO_CODE") != "${CLAUDE_PLUGIN_ROOT}/src":
        fail("unexpected Claude plugin NEXO_CODE")

    if not HOOKS_JSON.is_file():
        fail("hooks/hooks.json is missing")
    if not SERVER_PY.is_file():
        fail("src/server.py is missing")

    print("[claude-plugin] OK")


if __name__ == "__main__":
    main()
