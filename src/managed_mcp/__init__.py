"""Managed MCP capability catalog and client-config helpers."""

from .catalog import (
    CATALOG_PATH,
    LOCK_PATH,
    ManagedCapability,
    ManagedProvider,
    build_managed_server_entries,
    load_catalog,
    load_lock,
    provider_for_capability,
    validate_catalog_lock,
)
from .client_config import merge_json_mcp_servers, merge_toml_mcp_servers
from .reconcile import managed_mcp_status, reconcile_managed_mcp

__all__ = [
    "CATALOG_PATH",
    "LOCK_PATH",
    "ManagedCapability",
    "ManagedProvider",
    "build_managed_server_entries",
    "load_catalog",
    "load_lock",
    "provider_for_capability",
    "validate_catalog_lock",
    "merge_json_mcp_servers",
    "merge_toml_mcp_servers",
    "managed_mcp_status",
    "reconcile_managed_mcp",
]
