"""State watchers plugin — persistent drift/health/expiry watchers."""

from __future__ import annotations

import json

from db import create_state_watcher, list_state_watchers, update_state_watcher
from state_watchers_runtime import run_state_watchers


def handle_state_watcher_create(
    watcher_type: str,
    title: str,
    target: str = "",
    severity: str = "warn",
    status: str = "active",
    config: str = "{}",
) -> str:
    """Create a persistent state watcher for drift, health, or expiry."""
    try:
        watcher = create_state_watcher(
            watcher_type,
            title,
            target=target,
            severity=severity,
            status=status,
            config=json.loads(config) if str(config).strip() else {},
        )
    except (ValueError, json.JSONDecodeError) as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2)
    return json.dumps({"ok": True, "watcher": watcher}, ensure_ascii=False, indent=2)


def handle_state_watcher_update(
    watcher_id: str,
    title: str = "",
    target: str = "",
    severity: str = "",
    status: str = "",
    config: str = "",
) -> str:
    """Update an existing state watcher."""
    payload = None
    if str(config).strip():
        try:
            payload = json.loads(config)
        except json.JSONDecodeError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2)
    watcher = update_state_watcher(
        watcher_id,
        title=(title or None),
        target=(target or None),
        severity=(severity or None),
        status=(status or None),
        config=payload,
    )
    if not watcher:
        return json.dumps({"ok": False, "error": f"Unknown watcher_id: {watcher_id}"}, ensure_ascii=False, indent=2)
    return json.dumps({"ok": True, "watcher": watcher}, ensure_ascii=False, indent=2)


def handle_state_watcher_list(status: str = "", watcher_type: str = "", limit: int = 50) -> str:
    """List configured state watchers."""
    watchers = list_state_watchers(status=status, watcher_type=watcher_type, limit=max(1, int(limit or 50)))
    return json.dumps({"ok": True, "count": len(watchers), "watchers": watchers}, ensure_ascii=False, indent=2)


def handle_state_watcher_run(status: str = "active", persist: bool = True) -> str:
    """Run active state watchers and return their current health."""
    summary = run_state_watchers(status=status or "active", persist=bool(persist))
    return json.dumps({"ok": True, **summary}, ensure_ascii=False, indent=2)


TOOLS = [
    (handle_state_watcher_create, "nexo_state_watcher_create", "Create a persistent state watcher for repo drift, cron drift, API health, environment drift, or expiry."),
    (handle_state_watcher_update, "nexo_state_watcher_update", "Update an existing persistent state watcher."),
    (handle_state_watcher_list, "nexo_state_watcher_list", "List persistent state watchers."),
    (handle_state_watcher_run, "nexo_state_watcher_run", "Run persistent state watchers and return their current health summary."),
]
