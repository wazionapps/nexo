from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("catalog.json")
LOCK_PATH = Path(__file__).with_name("lock.json")
CLIENT_KEYS = {"claude_code", "claude_desktop", "codex"}


@dataclass(frozen=True)
class ManagedProvider:
    id: str
    package: str
    source_type: str
    platforms: tuple[str, ...]
    fallback: bool = False
    risk: str = ""
    version: str = ""


@dataclass(frozen=True)
class ManagedCapability:
    id: str
    display_name: str
    enabled_by_default: bool
    risk: str
    clients: tuple[str, ...]
    providers: tuple[ManagedProvider, ...]


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    return _load_json(path or CATALOG_PATH)


def load_lock(path: Path | None = None) -> dict[str, Any]:
    return _load_json(path or LOCK_PATH)


def _platform_key(platform: str | None = None) -> str:
    value = (platform or sys.platform or "").lower()
    if value.startswith("darwin"):
        return "darwin"
    if value.startswith(("win32", "cygwin", "msys")) or os.name == "nt":
        return "win32"
    if value.startswith("linux"):
        return "linux"
    return value


def validate_catalog_lock(
    catalog: dict[str, Any] | None = None,
    lock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = catalog or load_catalog()
    lock = lock or load_lock()
    errors: list[str] = []
    warnings: list[str] = []

    if catalog.get("schema") != "nexo.managed_mcp.catalog.v1":
        errors.append("catalog schema mismatch")
    if lock.get("schema") != "nexo.managed_mcp.lock.v1":
        errors.append("lock schema mismatch")
    if catalog.get("catalog_version") != lock.get("catalog_version"):
        errors.append("catalog_version mismatch")

    lock_providers = lock.get("providers")
    if not isinstance(lock_providers, dict):
        errors.append("lock providers must be an object")
        lock_providers = {}

    capabilities = catalog.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        errors.append("catalog capabilities must be a non-empty list")
        capabilities = []

    capability_ids: set[str] = set()
    required_provider_ids: set[str] = set()
    for capability in capabilities:
        if not isinstance(capability, dict):
            errors.append("capability entries must be objects")
            continue
        capability_id = str(capability.get("id") or "").strip()
        if not capability_id:
            errors.append("capability without id")
            continue
        if capability_id in capability_ids:
            errors.append(f"duplicate capability id: {capability_id}")
        capability_ids.add(capability_id)
        clients = capability.get("clients")
        if not isinstance(clients, list) or not clients:
            errors.append(f"{capability_id}: clients must be non-empty")
        elif any(str(client) not in CLIENT_KEYS for client in clients):
            errors.append(f"{capability_id}: unknown client in clients")
        providers = capability.get("providers")
        if not isinstance(providers, list) or not providers:
            errors.append(f"{capability_id}: providers must be non-empty")
            continue
        platforms_by_provider: set[str] = set()
        for provider in providers:
            if not isinstance(provider, dict):
                errors.append(f"{capability_id}: provider entries must be objects")
                continue
            provider_id = str(provider.get("id") or "").strip()
            source = provider.get("source") if isinstance(provider.get("source"), dict) else {}
            package = str(source.get("package") or "").strip()
            platforms = provider.get("platforms")
            if not provider_id:
                errors.append(f"{capability_id}: provider without id")
                continue
            required_provider_ids.add(provider_id)
            if not package:
                errors.append(f"{provider_id}: source.package missing")
            if not isinstance(platforms, list) or not platforms:
                errors.append(f"{provider_id}: platforms missing")
            else:
                platforms_by_provider.update(str(item) for item in platforms)
            if provider.get("version_policy") not in {"locked", "latest_on_release"}:
                errors.append(f"{provider_id}: unsupported version_policy")
            locked = lock_providers.get(provider_id)
            if not isinstance(locked, dict):
                errors.append(f"{provider_id}: missing from lockfile")
            elif locked.get("package") != package:
                errors.append(f"{provider_id}: lock package mismatch")
            elif "@latest" in str(locked.get("version") or ""):
                errors.append(f"{provider_id}: lock version must not use @latest")
            elif str(locked.get("version") or "").strip() in {"", "0.0.0-managed"}:
                errors.append(f"{provider_id}: lock version must be an exact package version")
            elif str(locked.get("source_type") or "") == "npm":
                if not str(locked.get("integrity") or "").strip():
                    errors.append(f"{provider_id}: npm lock integrity missing")
                if not str(locked.get("tarball") or "").strip():
                    errors.append(f"{provider_id}: npm lock tarball missing")
                if not str(locked.get("bin") or "").strip():
                    errors.append(f"{provider_id}: npm lock bin missing")
        if capability.get("enabled_by_default") is True:
            for required_platform in ("darwin", "win32"):
                if required_platform not in platforms_by_provider:
                    errors.append(f"{capability_id}: missing {required_platform} provider")

    extra = set(lock_providers) - required_provider_ids
    if extra:
        warnings.append("lock contains unused providers: " + ", ".join(sorted(extra)))

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "catalog_version": str(catalog.get("catalog_version") or ""),
        "capabilities": sorted(capability_ids),
        "providers": sorted(required_provider_ids),
    }


def provider_for_capability(
    capability: dict[str, Any],
    *,
    platform: str | None = None,
) -> dict[str, Any] | None:
    platform_key = _platform_key(platform)
    providers = capability.get("providers")
    if not isinstance(providers, list):
        return None
    exact: list[dict[str, Any]] = []
    fallbacks: list[dict[str, Any]] = []
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        platforms = provider.get("platforms")
        if not isinstance(platforms, list) or platform_key not in {str(p) for p in platforms}:
            continue
        if provider.get("fallback"):
            fallbacks.append(provider)
        else:
            exact.append(provider)
    return (exact or fallbacks or [None])[0]


def _runner_path(nexo_home: Path, runtime_root: Path | None = None) -> Path:
    runtime_bin = nexo_home / "bin" / "nexo-managed-mcp"
    if runtime_bin.exists():
        return runtime_bin
    runtime_js = nexo_home / "bin" / "nexo-managed-mcp.js"
    if runtime_js.exists():
        return runtime_js
    runtime_bin = nexo_home / "runtime" / "bin" / "nexo-managed-mcp"
    if runtime_bin.exists():
        return runtime_bin
    if runtime_root:
        candidate = runtime_root / "bin" / "nexo-managed-mcp.js"
        if candidate.exists():
            return candidate
        sibling = runtime_root.parent / "bin" / "nexo-managed-mcp.js"
        if sibling.exists():
            return sibling
    return runtime_bin


def _entry_digest(entry: dict[str, Any]) -> str:
    body = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def build_managed_server_entries(
    *,
    client: str,
    nexo_home: str | os.PathLike[str] | Path,
    runtime_root: str | os.PathLike[str] | Path | None = None,
    catalog: dict[str, Any] | None = None,
    lock: dict[str, Any] | None = None,
    platform: str | None = None,
) -> dict[str, dict[str, Any]]:
    catalog = catalog or load_catalog()
    lock = lock or load_lock()
    validation = validate_catalog_lock(catalog, lock)
    if not validation["ok"]:
        raise ValueError("; ".join(validation["errors"]))
    nexo_home_path = Path(nexo_home).expanduser()
    runtime_root_path = Path(runtime_root).expanduser() if runtime_root else None
    runner = _runner_path(nexo_home_path, runtime_root_path)
    lock_providers = lock.get("providers") if isinstance(lock.get("providers"), dict) else {}
    entries: dict[str, dict[str, Any]] = {}
    for capability in catalog.get("capabilities") or []:
        if not isinstance(capability, dict):
            continue
        if not capability.get("enabled_by_default"):
            continue
        clients = {str(item) for item in capability.get("clients") or []}
        if client not in clients:
            continue
        capability_id = str(capability.get("id") or "").strip()
        provider = provider_for_capability(capability, platform=platform)
        if not capability_id or not provider:
            continue
        provider_id = str(provider.get("id") or "").strip()
        locked = lock_providers.get(provider_id) if isinstance(lock_providers, dict) else {}
        name = f"nexo_{capability_id}"
        entry = {
            "command": str(runner),
            "args": ["run", capability_id],
            "env": {"NEXO_HOME": str(nexo_home_path)},
            "nexo": {
                "owner": "nexo",
                "schema": "nexo.managed_mcp.client.v1",
                "capability_id": capability_id,
                "provider_id": provider_id,
                "provider_package": str((locked or {}).get("package") or ""),
                "provider_version": str((locked or {}).get("version") or ""),
                "provider_bin": str((locked or {}).get("bin") or ""),
                "risk": str(capability.get("risk") or ""),
            },
        }
        if runtime_root_path:
            entry["env"]["NEXO_CODE"] = str(runtime_root_path)
        entry["nexo"]["config_digest"] = _entry_digest(entry)
        entries[name] = entry
    return entries
