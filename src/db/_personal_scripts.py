from __future__ import annotations
"""NEXO DB — Personal scripts registry.

Filesystem remains the source of truth for personal scripts in NEXO_HOME/scripts/.
SQLite stores operational metadata so NEXO can reason about what scripts exist,
what they do, and which schedules/plists are attached to them.
"""

import datetime
import json
import os
from pathlib import Path

from db._core import get_db


NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


def _now_text() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _row_to_dict(row) -> dict:
    return dict(row) if row is not None else {}


def _json_load(value, default):
    if value in ("", None):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
    except Exception:
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _json_dump(value, default):
    if value in ("", None):
        value = default
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            value = parsed
        except Exception:
            return json.dumps(default, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def _safe_slug(value: str) -> str:
    chars: list[str] = []
    for ch in value.lower():
        if ch.isalnum():
            chars.append(ch)
        elif ch in {"-", "_"}:
            chars.append("-")
    slug = "".join(chars).strip("-")
    return slug or "script"


def _ensure_script_id(conn, name: str, path: str) -> str:
    existing = conn.execute(
        "SELECT id FROM personal_scripts WHERE path = ? LIMIT 1",
        (path,),
    ).fetchone()
    if existing:
        return existing["id"]

    base = f"ps-{_safe_slug(name)}"
    candidate = base
    suffix = 2
    while conn.execute("SELECT 1 FROM personal_scripts WHERE id = ?", (candidate,)).fetchone():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def upsert_personal_script(
    *,
    name: str,
    path: str,
    description: str = "",
    runtime: str = "unknown",
    metadata: dict | None = None,
    created_by: str = "manual",
    source: str = "filesystem",
    enabled: bool = True,
    has_inline_metadata: bool = False,
) -> dict:
    conn = get_db()
    script_id = _ensure_script_id(conn, name, path)
    now = _now_text()
    conn.execute(
        """
        INSERT INTO personal_scripts (
            id, name, path, description, runtime, metadata_json, created_by, source,
            enabled, has_inline_metadata, last_synced_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            runtime = excluded.runtime,
            metadata_json = excluded.metadata_json,
            created_by = COALESCE(NULLIF(personal_scripts.created_by, ''), excluded.created_by),
            source = excluded.source,
            enabled = excluded.enabled,
            has_inline_metadata = excluded.has_inline_metadata,
            last_synced_at = excluded.last_synced_at,
            updated_at = excluded.updated_at
        """,
        (
            script_id,
            name,
            path,
            description,
            runtime,
            _json_dump(metadata or {}, {}),
            created_by,
            source,
            1 if enabled else 0,
            1 if has_inline_metadata else 0,
            now,
            now,
            now,
        ),
    )
    row = conn.execute("SELECT * FROM personal_scripts WHERE path = ?", (path,)).fetchone()
    return hydrate_personal_script(_row_to_dict(row))


def delete_missing_personal_scripts(active_paths: list[str]) -> int:
    conn = get_db()
    if active_paths:
        placeholders = ",".join("?" for _ in active_paths)
        rows = conn.execute(
            f"SELECT id FROM personal_scripts WHERE path NOT IN ({placeholders})",
            tuple(active_paths),
        ).fetchall()
    else:
        rows = conn.execute("SELECT id FROM personal_scripts").fetchall()

    count = len(rows)
    for row in rows:
        conn.execute("DELETE FROM personal_scripts WHERE id = ?", (row["id"],))
    return count


def register_personal_script_schedule(
    *,
    script_path: str,
    cron_id: str,
    schedule_type: str,
    schedule_value: str,
    schedule_label: str = "",
    launchd_label: str = "",
    plist_path: str = "",
    description: str = "",
    enabled: bool = True,
) -> dict | None:
    conn = get_db()
    script = conn.execute(
        "SELECT id FROM personal_scripts WHERE path = ?",
        (script_path,),
    ).fetchone()
    if not script:
        return None

    now = _now_text()
    conn.execute(
        """
        INSERT INTO personal_script_schedules (
            script_id, cron_id, schedule_type, schedule_value, schedule_label,
            launchd_label, plist_path, description, enabled, last_synced_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cron_id) DO UPDATE SET
            script_id = excluded.script_id,
            schedule_type = excluded.schedule_type,
            schedule_value = excluded.schedule_value,
            schedule_label = excluded.schedule_label,
            launchd_label = excluded.launchd_label,
            plist_path = excluded.plist_path,
            description = excluded.description,
            enabled = excluded.enabled,
            last_synced_at = excluded.last_synced_at,
            updated_at = excluded.updated_at
        """,
        (
            script["id"],
            cron_id,
            schedule_type,
            schedule_value,
            schedule_label,
            launchd_label,
            plist_path,
            description,
            1 if enabled else 0,
            now,
            now,
            now,
        ),
    )
    row = conn.execute(
        "SELECT * FROM personal_script_schedules WHERE cron_id = ?",
        (cron_id,),
    ).fetchone()
    return hydrate_personal_schedule(_row_to_dict(row))


def delete_missing_personal_schedules(active_cron_ids: list[str]) -> int:
    conn = get_db()
    if active_cron_ids:
        placeholders = ",".join("?" for _ in active_cron_ids)
        rows = conn.execute(
            f"SELECT cron_id FROM personal_script_schedules WHERE cron_id NOT IN ({placeholders})",
            tuple(active_cron_ids),
        ).fetchall()
    else:
        rows = conn.execute("SELECT cron_id FROM personal_script_schedules").fetchall()

    count = len(rows)
    for row in rows:
        conn.execute("DELETE FROM personal_script_schedules WHERE cron_id = ?", (row["cron_id"],))
    return count


def list_personal_script_schedules(script_id: str = "", include_disabled: bool = True) -> list[dict]:
    conn = get_db()
    clauses = []
    params: list = []
    if script_id:
        clauses.append("script_id = ?")
        params.append(script_id)
    if not include_disabled:
        clauses.append("enabled = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM personal_script_schedules {where} ORDER BY cron_id ASC",
        tuple(params),
    ).fetchall()
    return [hydrate_personal_schedule(_row_to_dict(row)) for row in rows]


def get_personal_script_schedule(cron_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM personal_script_schedules WHERE cron_id = ?",
        (cron_id,),
    ).fetchone()
    return hydrate_personal_schedule(_row_to_dict(row)) if row else None


def delete_personal_script_schedule(cron_id: str) -> int:
    conn = get_db()
    result = conn.execute(
        "DELETE FROM personal_script_schedules WHERE cron_id = ?",
        (cron_id,),
    )
    return int(result.rowcount or 0)


def hydrate_personal_schedule(row: dict) -> dict:
    if not row:
        return {}
    row["enabled"] = bool(row.get("enabled", 1))
    return row


def _latest_cron_runs_by_id(cron_ids: list[str]) -> dict[str, dict]:
    conn = get_db()
    if not cron_ids:
        return {}
    placeholders = ",".join("?" for _ in cron_ids)
    rows = conn.execute(
        f"""
        SELECT c1.cron_id, c1.started_at, c1.exit_code
        FROM cron_runs c1
        JOIN (
            SELECT cron_id, MAX(id) AS max_id
            FROM cron_runs
            WHERE cron_id IN ({placeholders})
            GROUP BY cron_id
        ) latest ON latest.max_id = c1.id
        """,
        tuple(cron_ids),
    ).fetchall()
    return {
        row["cron_id"]: {
            "started_at": row["started_at"],
            "exit_code": row["exit_code"],
        }
        for row in rows
    }


def hydrate_personal_script(row: dict) -> dict:
    if not row:
        return {}
    row["enabled"] = bool(row.get("enabled", 1))
    row["has_inline_metadata"] = bool(row.get("has_inline_metadata", 0))
    row["metadata"] = _json_load(row.pop("metadata_json", "{}"), {})
    return row


def list_personal_scripts(include_disabled: bool = True) -> list[dict]:
    conn = get_db()
    where = "" if include_disabled else "WHERE enabled = 1"
    rows = conn.execute(
        f"SELECT * FROM personal_scripts {where} ORDER BY name COLLATE NOCASE ASC"
    ).fetchall()
    scripts = [hydrate_personal_script(_row_to_dict(row)) for row in rows]
    if not scripts:
        return []

    schedules_by_script: dict[str, list[dict]] = {}
    cron_ids: list[str] = []
    for schedule in list_personal_script_schedules(include_disabled=include_disabled):
        schedules_by_script.setdefault(schedule["script_id"], []).append(schedule)
        cron_ids.append(schedule["cron_id"])

    latest_runs = _latest_cron_runs_by_id(cron_ids)
    for script in scripts:
        script_schedules = schedules_by_script.get(script["id"], [])
        for schedule in script_schedules:
            latest = latest_runs.get(schedule["cron_id"])
            if latest:
                schedule["last_run_at"] = latest["started_at"]
                schedule["last_exit_code"] = latest["exit_code"]
        script["schedules"] = script_schedules
        script["has_schedule"] = bool(script_schedules)
        latest = None
        for schedule in script_schedules:
            started_at = schedule.get("last_run_at")
            if started_at and (latest is None or started_at > latest.get("started_at", "")):
                latest = {"started_at": started_at, "exit_code": schedule.get("last_exit_code")}
        if latest:
            script["last_run_at"] = latest["started_at"]
            script["last_exit_code"] = latest["exit_code"]
    return scripts


def get_personal_script(name_or_path: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        """
        SELECT * FROM personal_scripts
        WHERE path = ? OR name = ?
        ORDER BY path = ? DESC
        LIMIT 1
        """,
        (name_or_path, name_or_path, name_or_path),
    ).fetchone()
    if not row:
        return None
    script = hydrate_personal_script(_row_to_dict(row))
    script["schedules"] = list_personal_script_schedules(script["id"])
    script["has_schedule"] = bool(script["schedules"])
    return script


def delete_personal_script(name_or_path: str) -> int:
    conn = get_db()
    result = conn.execute(
        "DELETE FROM personal_scripts WHERE path = ? OR name = ? OR id = ?",
        (name_or_path, name_or_path, name_or_path),
    )
    return int(result.rowcount or 0)


def record_personal_script_run(name_or_path: str, exit_code: int, run_at: str | None = None) -> None:
    conn = get_db()
    run_at = run_at or _now_text()
    conn.execute(
        """
        UPDATE personal_scripts
        SET last_run_at = ?, last_exit_code = ?, updated_at = ?
        WHERE path = ? OR name = ?
        """,
        (run_at, exit_code, _now_text(), name_or_path, name_or_path),
    )


def sync_personal_scripts_registry(
    script_records: list[dict],
    schedule_records: list[dict] | None = None,
    *,
    prune_missing: bool = True,
) -> dict:
    schedule_records = schedule_records or []
    active_paths: list[str] = []
    upserted = 0
    scheduled = 0

    for record in script_records:
        path = str(record["path"])
        active_paths.append(path)
        upsert_personal_script(
            name=record.get("name") or Path(path).stem,
            path=path,
            description=record.get("description", ""),
            runtime=record.get("runtime", "unknown"),
            metadata=record.get("metadata", {}),
            created_by=record.get("created_by", "manual"),
            source=record.get("source", "filesystem"),
            enabled=record.get("enabled", True),
            has_inline_metadata=bool(record.get("metadata")),
        )
        upserted += 1

    pruned_scripts = delete_missing_personal_scripts(active_paths) if prune_missing else 0

    active_cron_ids: list[str] = []
    for schedule in schedule_records:
        cron_id = schedule.get("cron_id")
        script_path = schedule.get("script_path")
        if not cron_id or not script_path:
            continue
        active_cron_ids.append(cron_id)
        registered = register_personal_script_schedule(
            script_path=script_path,
            cron_id=cron_id,
            schedule_type=schedule.get("schedule_type", ""),
            schedule_value=schedule.get("schedule_value", ""),
            schedule_label=schedule.get("schedule_label", ""),
            launchd_label=schedule.get("launchd_label", ""),
            plist_path=schedule.get("plist_path", ""),
            description=schedule.get("description", ""),
            enabled=schedule.get("enabled", True),
        )
        if registered:
            scheduled += 1

    pruned_schedules = delete_missing_personal_schedules(active_cron_ids) if prune_missing else 0
    return {
        "ok": True,
        "scripts_upserted": upserted,
        "schedules_upserted": scheduled,
        "scripts_pruned": pruned_scripts,
        "schedules_pruned": pruned_schedules,
        "registered_scripts": len(list_personal_scripts()),
    }


def get_personal_script_health_report(*, fix: bool = False) -> dict:
    if fix:
        from script_registry import reconcile_personal_scripts

        reconcile_personal_scripts(dry_run=False)

    from script_registry import classify_scripts_dir, audit_personal_schedules

    issues: list[dict] = []
    scripts = list_personal_scripts()
    checked = 0
    audit = audit_personal_schedules()
    audit_by_path: dict[str, list[dict]] = {}
    for schedule in audit.get("schedules", []):
        audit_by_path.setdefault(schedule.get("script_path", ""), []).append(schedule)

    classified = classify_scripts_dir()
    personal_entries = [entry for entry in classified.get("entries", []) if entry.get("classification") == "personal"]

    for script in scripts:
        checked += 1
        path = Path(script["path"])
        if not path.is_file():
            issues.append({
                "script_id": script["id"],
                "severity": "error",
                "message": f"missing file: {path}",
            })
        for schedule in script.get("schedules", []):
            checked += 1
            plist_path = schedule.get("plist_path", "")
            if plist_path and not Path(plist_path).is_file():
                issues.append({
                    "script_id": script["id"],
                    "severity": "warn",
                    "message": f"missing managed plist for cron {schedule['cron_id']}: {plist_path}",
                })

    for entry in personal_entries:
        checked += 1
        declared = entry.get("declared_schedule", {})
        if declared.get("required") and not declared.get("valid"):
            issues.append({
                "script_id": f"declared:{entry['name']}",
                "severity": "error",
                "message": f"invalid declared schedule for {entry['name']}: {declared.get('error', 'invalid metadata')}",
            })
            continue

        if not declared.get("required"):
            continue

        managed = [
            item for item in audit_by_path.get(entry["path"], [])
            if item.get("schedule_managed")
        ]
        if managed:
            continue

        related = audit_by_path.get(entry["path"], [])
        reason = "no schedule discovered"
        if related:
            states = ", ".join(item.get("schedule_state", item.get("schedule_origin", "unknown")) for item in related)
            reason = f"discovered but not managed ({states})"
        issues.append({
            "script_id": f"declared:{entry['name']}",
            "severity": "warn",
            "message": (
                f"missing declared managed schedule for {entry['name']}: "
                f"{declared.get('schedule_label', declared.get('cron_id', ''))} [{reason}]"
            ),
        })

    for schedule in audit.get("schedules", []):
        checked += 1
        if schedule.get("schedule_managed") and schedule.get("schedule_type") == "keep_alive":
            runtime_state = str(schedule.get("runtime_state", "") or "")
            runtime_summary = str(schedule.get("runtime_summary", "") or runtime_state or "runtime issue")
            if runtime_state in {"degraded", "stale", "duplicated"}:
                severity = "error" if runtime_state == "duplicated" else "warn"
                issues.append({
                    "script_id": schedule.get("script_name") or schedule.get("script_path") or schedule.get("cron_id"),
                    "severity": severity,
                    "message": (
                        f"keep_alive runtime {schedule['cron_id']}: {runtime_summary}"
                    ),
                })
        if schedule.get("schedule_managed"):
            continue

        severity = "warn"
        if schedule.get("schedule_origin") == "orphan_schedule":
            severity = "error"
        elif schedule.get("schedule_declared") and schedule.get("schedule_matches_declared") is False:
            severity = "error"

        label = schedule.get("schedule_label") or schedule.get("schedule_value") or schedule.get("schedule_type")
        problems = "; ".join(schedule.get("problems", [])) or schedule.get("schedule_state", "schedule issue")
        target = schedule.get("script_name") or schedule.get("script_path") or schedule.get("cron_id")
        issues.append({
            "script_id": target,
            "severity": severity,
            "message": (
                f"{schedule.get('schedule_origin', 'schedule')} {schedule['cron_id']} "
                f"({label}) for {target}: {problems}"
            ),
        })

    return {
        "checked": checked,
        "scripts": len(scripts),
        "schedules": sum(len(script.get("schedules", [])) for script in scripts),
        "issues": issues,
        "fixed": bool(fix),
        "schedule_audit": audit,
    }
