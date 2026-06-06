from __future__ import annotations

from copy import deepcopy
from typing import Any


def _is_nexo_owned(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    meta = entry.get("nexo")
    return isinstance(meta, dict) and meta.get("owner") == "nexo"


def merge_json_mcp_servers(payload: dict[str, Any], entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    result = deepcopy(payload) if isinstance(payload, dict) else {}
    servers = result.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = {}
        result["mcpServers"] = servers
    metadata = result.setdefault("nexo", {})
    if not isinstance(metadata, dict):
        metadata = {}
        result["nexo"] = metadata
    managed = metadata.setdefault("managed_mcp", {})
    if not isinstance(managed, dict):
        managed = {}
        metadata["managed_mcp"] = managed
    managed_servers = managed.setdefault("servers", {})
    if not isinstance(managed_servers, dict):
        managed_servers = {}
        managed["servers"] = managed_servers

    for name, entry in entries.items():
        current = servers.get(name)
        if current is not None and not _is_nexo_owned(current):
            continue
        servers[name] = deepcopy(entry)
        managed_servers[name] = deepcopy(entry.get("nexo") or {})
    managed["schema"] = "nexo.managed_mcp.client.v1"
    return result


def merge_toml_mcp_servers(payload: dict[str, Any], entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    result = deepcopy(payload) if isinstance(payload, dict) else {}
    servers = result.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        servers = {}
        result["mcp_servers"] = servers
    nexo_table = result.setdefault("nexo", {})
    if not isinstance(nexo_table, dict):
        nexo_table = {}
        result["nexo"] = nexo_table
    managed = nexo_table.setdefault("managed_mcp", {})
    if not isinstance(managed, dict):
        managed = {}
        nexo_table["managed_mcp"] = managed
    managed_servers = managed.setdefault("servers", {})
    if not isinstance(managed_servers, dict):
        managed_servers = {}
        managed["servers"] = managed_servers

    for name, entry in entries.items():
        current = servers.get(name)
        current_meta = managed_servers.get(name)
        if current is not None and not (
            _is_nexo_owned(current) or (isinstance(current_meta, dict) and current_meta.get("owner") == "nexo")
        ):
            continue
        servers[name] = {
            "command": entry.get("command", ""),
            "args": list(entry.get("args", []) or []),
            "env": dict(entry.get("env", {}) or {}),
        }
        managed_servers[name] = deepcopy(entry.get("nexo") or {})
    managed["schema"] = "nexo.managed_mcp.client.v1"
    return result
