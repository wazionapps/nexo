from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .catalog import build_managed_server_entries, load_catalog, load_lock, validate_catalog_lock


def _state_dir(nexo_home: Path) -> Path:
    return nexo_home / "runtime" / "managed-mcp"


def _state_path(nexo_home: Path) -> Path:
    return _state_dir(nexo_home) / "installed-state.json"


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


def reconcile_managed_mcp(
    *,
    nexo_home: str | os.PathLike[str] | Path,
    runtime_root: str | os.PathLike[str] | Path | None = None,
    clients: list[str] | tuple[str, ...] | None = None,
    apply: bool = False,
    platform: str | None = None,
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

    state = {
        "schema": "nexo.managed_mcp.state.v1",
        "catalog_version": catalog.get("catalog_version", ""),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "validation": validation,
        "desired": desired,
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
    }
