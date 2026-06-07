from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .catalog import build_managed_server_entries, load_catalog, load_lock, validate_catalog_lock


def _state_dir(nexo_home: Path) -> Path:
    return nexo_home / "runtime" / "managed-mcp"


def _state_path(nexo_home: Path) -> Path:
    return _state_dir(nexo_home) / "installed-state.json"


def _artifacts_dir(nexo_home: Path) -> Path:
    return _state_dir(nexo_home) / "artifacts"


def _provider_root(nexo_home: Path, provider_id: str) -> Path:
    return _artifacts_dir(nexo_home) / provider_id


def _provider_stage_dir(nexo_home: Path, provider_id: str, version: str) -> Path:
    return _provider_root(nexo_home, provider_id) / version


def _provider_wrapper_path(nexo_home: Path, provider_id: str, *, platform: str | None = None) -> Path:
    suffix = ".cmd" if str(platform or os.name).lower().startswith(("win", "nt")) else ""
    return _provider_root(nexo_home, provider_id) / "bin" / f"{provider_id}{suffix}"


def _read_state(nexo_home: Path) -> dict[str, Any]:
    path = _state_path(nexo_home)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(nexo_home: Path, state: dict[str, Any]) -> None:
    directory = _state_dir(nexo_home)
    directory.mkdir(parents=True, exist_ok=True)
    path = _state_path(nexo_home)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _provider_ids_from_desired(desired: dict[str, Any]) -> set[str]:
    provider_ids: set[str] = set()
    for client_entries in desired.values():
        if not isinstance(client_entries, dict):
            continue
        for entry in client_entries.values():
            meta = entry.get("nexo") if isinstance(entry, dict) else {}
            if isinstance(meta, dict) and meta.get("provider_id"):
                provider_ids.add(str(meta["provider_id"]))
    return provider_ids


def _locked_providers(lock: dict[str, Any]) -> dict[str, dict[str, Any]]:
    providers = lock.get("providers") if isinstance(lock.get("providers"), dict) else {}
    return {str(key): value for key, value in providers.items() if isinstance(value, dict)}


def _run_npm_install(stage_dir: Path, package: str, version: str) -> subprocess.CompletedProcess:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    return subprocess.run(
        [
            npm,
            "install",
            "--prefix",
            str(stage_dir),
            "--omit=dev",
            "--no-audit",
            "--no-fund",
            "--package-lock=false",
            f"{package}@{version}",
        ],
        text=True,
        capture_output=True,
        timeout=180,
    )


def _write_provider_wrappers(
    *,
    nexo_home: Path,
    provider_id: str,
    version: str,
    provider_bin: str,
) -> dict[str, str]:
    root = _provider_root(nexo_home, provider_id)
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stage_dir = _provider_stage_dir(nexo_home, provider_id, version)
    unix_target = stage_dir / "node_modules" / ".bin" / provider_bin
    win_target = stage_dir / "node_modules" / ".bin" / f"{provider_bin}.cmd"
    unix_wrapper = bin_dir / provider_id
    unix_wrapper.write_text(f"#!/bin/sh\nexec {json.dumps(str(unix_target))} \"$@\"\n")
    unix_wrapper.chmod(0o755)
    win_wrapper = bin_dir / f"{provider_id}.cmd"
    win_wrapper.write_text(f"@echo off\r\ncall \"{win_target}\" %*\r\n")
    return {
        "unix": str(unix_wrapper),
        "win32": str(win_wrapper),
        "target": str(unix_target),
        "target_win32": str(win_target),
    }


def _stage_provider(
    *,
    nexo_home: Path,
    provider_id: str,
    locked: dict[str, Any],
    npm_runner=_run_npm_install,
) -> dict[str, Any]:
    package = str(locked.get("package") or "").strip()
    version = str(locked.get("version") or "").strip()
    provider_bin = str(locked.get("bin") or "").strip()
    if not package or not version or not provider_bin:
        return {"status": "failed", "error": "provider lock is incomplete"}
    stage_dir = _provider_stage_dir(nexo_home, provider_id, version)
    root = _provider_root(nexo_home, provider_id)
    tmp_dir = root / f".stage-{version}-{int(time.time() * 1000)}"
    root.mkdir(parents=True, exist_ok=True)
    if stage_dir.is_dir():
        wrappers = _write_provider_wrappers(
            nexo_home=nexo_home,
            provider_id=provider_id,
            version=version,
            provider_bin=provider_bin,
        )
        return {
            "status": "healthy",
            "version": version,
            "package": package,
            "staged_path": str(stage_dir),
            "executable": wrappers["unix"],
            "executable_win32": wrappers["win32"],
            "reused": True,
        }
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        result = npm_runner(tmp_dir, package, version)
        if getattr(result, "returncode", 1) != 0:
            return {
                "status": "failed",
                "version": version,
                "package": package,
                "error": (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "npm install failed")[-1200:],
            }
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        tmp_dir.replace(stage_dir)
        wrappers = _write_provider_wrappers(
            nexo_home=nexo_home,
            provider_id=provider_id,
            version=version,
            provider_bin=provider_bin,
        )
        return {
            "status": "healthy",
            "version": version,
            "package": package,
            "staged_path": str(stage_dir),
            "executable": wrappers["unix"],
            "executable_win32": wrappers["win32"],
            "integrity": str(locked.get("integrity") or ""),
        }
    except Exception as exc:
        return {"status": "failed", "version": version, "package": package, "error": str(exc)}
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _provider_health(
    *,
    nexo_home: Path,
    provider_id: str,
    locked: dict[str, Any],
    platform: str | None = None,
) -> dict[str, Any]:
    version = str(locked.get("version") or "").strip()
    executable = _provider_wrapper_path(nexo_home, provider_id, platform=platform)
    stage_dir = _provider_stage_dir(nexo_home, provider_id, version) if version else _provider_root(nexo_home, provider_id)
    if not version:
        return {"status": "failed", "reason": "missing_version"}
    if not stage_dir.is_dir():
        return {"status": "unstaged", "version": version, "staged_path": str(stage_dir)}
    if not executable.exists():
        return {"status": "failed", "version": version, "reason": "wrapper_missing", "executable": str(executable)}
    return {
        "status": "healthy",
        "version": version,
        "staged_path": str(stage_dir),
        "executable": str(executable),
    }


def reconcile_managed_mcp(
    *,
    nexo_home: str | os.PathLike[str] | Path,
    runtime_root: str | os.PathLike[str] | Path | None = None,
    clients: list[str] | tuple[str, ...] | None = None,
    apply: bool = False,
    platform: str | None = None,
    npm_runner=_run_npm_install,
) -> dict[str, Any]:
    nexo_home_path = Path(nexo_home).expanduser()
    catalog = load_catalog()
    lock = load_lock()
    validation = validate_catalog_lock(catalog, lock)
    desired_clients = list(clients or ("claude_code", "claude_desktop", "codex"))
    desired: dict[str, Any] = {}
    for client in desired_clients:
        desired[client] = build_managed_server_entries(
            client=client,
            nexo_home=nexo_home_path,
            runtime_root=runtime_root,
            catalog=catalog,
            lock=lock,
            platform=platform,
        )
    previous = _read_state(nexo_home_path)
    previous_desired = previous.get("desired") if isinstance(previous.get("desired"), dict) else {}
    actions: list[dict[str, str]] = []
    for client, entries in desired.items():
        old_entries = previous_desired.get(client) if isinstance(previous_desired, dict) else {}
        old_entries = old_entries if isinstance(old_entries, dict) else {}
        for name, entry in entries.items():
            if name not in old_entries:
                action = "install"
            elif old_entries.get(name) != entry:
                action = "update"
            else:
                action = "noop"
            actions.append({"client": client, "server": name, "action": action})
        for name in set(old_entries) - set(entries):
            actions.append({"client": client, "server": name, "action": "disable"})
    locked_by_provider = _locked_providers(lock)
    provider_ids = _provider_ids_from_desired(desired)
    provider_state: dict[str, Any] = {}
    if apply:
        for provider_id in sorted(provider_ids):
            provider_state[provider_id] = _stage_provider(
                nexo_home=nexo_home_path,
                provider_id=provider_id,
                locked=locked_by_provider.get(provider_id) or {},
                npm_runner=npm_runner,
            )
    else:
        for provider_id in sorted(provider_ids):
            provider_state[provider_id] = _provider_health(
                nexo_home=nexo_home_path,
                provider_id=provider_id,
                locked=locked_by_provider.get(provider_id) or {},
                platform=platform,
            )
    if any((state.get("status") if isinstance(state, dict) else "") not in {"healthy"} for state in provider_state.values()):
        actions.append({"client": "runtime", "server": "managed_mcp", "action": "healthcheck"})

    state = {
        "schema": "nexo.managed_mcp.state.v1",
        "catalog_version": catalog.get("catalog_version", ""),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "validation": validation,
        "desired": desired,
        "providers": provider_state,
        "last_plan": actions,
    }
    if apply:
        _write_state(nexo_home_path, state)
    return {
        "ok": validation["ok"],
        "applied": bool(apply),
        "state_path": str(_state_path(nexo_home_path)),
        "validation": validation,
        "actions": actions,
        "desired_clients": sorted(desired),
        "providers": provider_state,
    }


def managed_mcp_status(
    *,
    nexo_home: str | os.PathLike[str] | Path,
    runtime_root: str | os.PathLike[str] | Path | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    nexo_home_path = Path(nexo_home).expanduser()
    state = _read_state(nexo_home_path)
    plan = reconcile_managed_mcp(
        nexo_home=nexo_home_path,
        runtime_root=runtime_root,
        apply=False,
        platform=platform,
    )
    return {
        "ok": plan["ok"],
        "state_exists": _state_path(nexo_home_path).is_file(),
        "state_path": str(_state_path(nexo_home_path)),
        "catalog_version": plan["validation"].get("catalog_version", ""),
        "validation": plan["validation"],
        "last_applied_at": state.get("updated_at", ""),
        "actions": plan["actions"],
        "providers": plan.get("providers", {}),
    }
