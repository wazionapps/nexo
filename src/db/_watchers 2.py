from __future__ import annotations
"""NEXO DB — state watchers registry."""

import json
import secrets
import time

from db._core import get_db

WATCHER_TYPES = {"repo_drift", "cron_drift", "api_health", "environment_drift", "expiry"}
WATCHER_STATUSES = {"active", "paused", "archived"}
WATCHER_HEALTH = {"unknown", "healthy", "degraded", "critical"}


def _watcher_id() -> str:
    return f"SW-{int(time.time())}-{secrets.randbelow(100000)}"


def _as_json(value, default):
    if value is None:
        value = default
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _parse_json(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _row_to_watcher(row) -> dict:
    watcher = dict(row)
    watcher["config"] = _parse_json(watcher.get("config"), {})
    watcher["last_result"] = _parse_json(watcher.get("last_result"), {})
    return watcher


def create_state_watcher(
    watcher_type: str,
    title: str,
    *,
    target: str = "",
    severity: str = "warn",
    status: str = "active",
    config=None,
) -> dict:
    clean_type = str(watcher_type or "").strip().lower()
    if clean_type not in WATCHER_TYPES:
        raise ValueError(f"Unsupported watcher_type: {watcher_type}")
    clean_status = str(status or "active").strip().lower()
    if clean_status not in WATCHER_STATUSES:
        clean_status = "active"
    watcher_id = _watcher_id()
    conn = get_db()
    conn.execute(
        """INSERT INTO state_watchers (
               watcher_id, watcher_type, title, target, severity, status, config,
               last_health, last_result, last_checked_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            watcher_id,
            clean_type,
            str(title or "").strip(),
            str(target or "").strip(),
            str(severity or "warn").strip().lower() or "warn",
            clean_status,
            _as_json(config, {}),
            "unknown",
            "{}",
            "",
        ),
    )
    conn.commit()
    return get_state_watcher(watcher_id) or {"watcher_id": watcher_id}


def get_state_watcher(watcher_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM state_watchers WHERE watcher_id = ?",
        (str(watcher_id or "").strip(),),
    ).fetchone()
    return _row_to_watcher(row) if row else None


def list_state_watchers(*, status: str = "", watcher_type: str = "", limit: int = 100) -> list[dict]:
    clauses: list[str] = []
    params: list[object] = []
    if status:
        clauses.append("status = ?")
        params.append(str(status).strip().lower())
    if watcher_type:
        clauses.append("watcher_type = ?")
        params.append(str(watcher_type).strip().lower())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, int(limit or 100)))
    conn = get_db()
    rows = conn.execute(
        f"""SELECT *
            FROM state_watchers
            {where}
            ORDER BY updated_at DESC, watcher_id DESC
            LIMIT ?""",
        tuple(params),
    ).fetchall()
    return [_row_to_watcher(row) for row in rows]


def update_state_watcher(
    watcher_id: str,
    *,
    title: str | None = None,
    target: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    config=None,
) -> dict | None:
    current = get_state_watcher(watcher_id)
    if not current:
        return None
    updates = {
        "title": current["title"] if title is None else str(title).strip(),
        "target": current["target"] if target is None else str(target).strip(),
        "severity": current["severity"] if severity is None else str(severity).strip().lower(),
        "status": current["status"] if status is None else str(status).strip().lower(),
        "config": _as_json(current["config"] if config is None else config, {}),
    }
    if updates["status"] not in WATCHER_STATUSES:
        updates["status"] = current["status"]
    conn = get_db()
    conn.execute(
        """UPDATE state_watchers
           SET title = ?, target = ?, severity = ?, status = ?, config = ?,
               updated_at = datetime('now')
           WHERE watcher_id = ?""",
        (
            updates["title"],
            updates["target"],
            updates["severity"],
            updates["status"],
            updates["config"],
            str(watcher_id).strip(),
        ),
    )
    conn.commit()
    return get_state_watcher(watcher_id)


def update_state_watcher_result(watcher_id: str, *, health: str, result=None, checked_at: str = "") -> dict | None:
    clean_health = str(health or "unknown").strip().lower()
    if clean_health not in WATCHER_HEALTH:
        clean_health = "unknown"
    conn = get_db()
    conn.execute(
        """UPDATE state_watchers
           SET last_health = ?, last_result = ?, last_checked_at = ?, updated_at = datetime('now')
           WHERE watcher_id = ?""",
        (
            clean_health,
            _as_json(result, {}),
            checked_at.strip(),
            str(watcher_id or "").strip(),
        ),
    )
    conn.commit()
    return get_state_watcher(watcher_id)
