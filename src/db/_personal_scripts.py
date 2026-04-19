from __future__ import annotations
"""NEXO DB — Personal scripts registry.

Filesystem remains the source of truth for personal scripts in NEXO_HOME/scripts/.
SQLite stores operational metadata so NEXO can reason about what scripts exist,
what they do, and which schedules/plists are attached to them.
"""

import datetime
import importlib
import json
import os
from pathlib import Path

import paths
from runtime_home import resolve_nexo_home


NEXO_HOME = resolve_nexo_home()


def _now_text() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _get_db():
    """Resolve db._core lazily so reload-heavy tests use the live connection module."""
    return importlib.import_module("db._core").get_db()


def _canonical_scripts_dir() -> Path:
    return paths.personal_scripts_dir()


def _canonical_script_dirs() -> list[Path]:
    legacy = resolve_nexo_home(os.environ.get("NEXO_HOME", str(NEXO_HOME))) / "scripts"
    ordered = [
        paths.personal_scripts_dir(),
        paths.core_scripts_dir(),
        paths.core_dev_scripts_dir(),
    ]
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in ordered:
        key = str(candidate.resolve(strict=False))
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    legacy_key = str(legacy.resolve(strict=False))
    if legacy_key not in seen:
        result.append(legacy)
    return result


def _normalize_script_path(path: str | Path) -> str:
    candidate = Path(path).expanduser()
    resolved = candidate.resolve(strict=False)
    canonical_dirs = _canonical_script_dirs()
    legacy_dir = resolve_nexo_home(os.environ.get("NEXO_HOME", str(NEXO_HOME))) / "scripts"

    for scripts_dir in canonical_dirs:
        if scripts_dir == legacy_dir:
            continue
        try:
            relative = resolved.relative_to(scripts_dir.resolve(strict=False))
        except Exception:
            continue
        return str(scripts_dir / relative)

    try:
        relative = resolved.relative_to(legacy_dir.resolve(strict=False))
    except Exception:
        return str(candidate)

    for scripts_dir in canonical_dirs:
        if scripts_dir == legacy_dir:
            continue
        target = scripts_dir / relative
        if target.exists():
            return str(target)
    return str(_canonical_scripts_dir() / relative)


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
    path = _normalize_script_path(path)
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
    origin: str = "user",
    enabled: bool = True,
    has_inline_metadata: bool = False,
) -> dict:
    conn = _get_db()
    path = _normalize_script_path(path)
    script_id = _ensure_script_id(conn, name, path)
    now = _now_text()
    conn.execute(
        """
        INSERT INTO personal_scripts (
            id, name, path, description, runtime, metadata_json, created_by, source,
            origin, enabled, has_inline_metadata, last_synced_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            runtime = excluded.runtime,
            metadata_json = excluded.metadata_json,
            created_by = COALESCE(NULLIF(personal_scripts.created_by, ''), excluded.created_by),
            source = excluded.source,
            origin = excluded.origin,
            -- Plan F0.2.2: preserve operator-set `enabled` flag across sync runs.
            -- Sync defaults to enabled=True for INSERTs; on UPDATE we keep
            -- whatever the operator (or `nexo scripts disable`) set.
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
            origin or "user",
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
    conn = _get_db()
    normalized_paths = [_normalize_script_path(path) for path in active_paths]
    if normalized_paths:
        placeholders = ",".join("?" for _ in normalized_paths)
        rows = conn.execute(
            f"SELECT id FROM personal_scripts "
            f"WHERE COALESCE(origin, 'user') != 'core' AND path NOT IN ({placeholders})",
            tuple(normalized_paths),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM personal_scripts WHERE COALESCE(origin, 'user') != 'core'"
        ).fetchall()

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
    conn = _get_db()
    script_path = _normalize_script_path(script_path)
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
    conn = _get_db()
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
    conn = _get_db()
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
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM personal_script_schedules WHERE cron_id = ?",
        (cron_id,),
    ).fetchone()
    return hydrate_personal_schedule(_row_to_dict(row)) if row else None


def delete_personal_script_schedule(cron_id: str) -> int:
    conn = _get_db()
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
    conn = _get_db()
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


def list_personal_scripts(include_disabled: bool = True, *, include_core: bool = False) -> list[dict]:
    conn = _get_db()
    clauses: list[str] = []
    if not include_disabled:
        clauses.append("enabled = 1")
    if not include_core:
        clauses.append("COALESCE(origin, 'user') != 'core'")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
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


def get_personal_script(name_or_path: str, *, include_core: bool = False) -> dict | None:
    conn = _get_db()
    normalized_path = _normalize_script_path(name_or_path)
    clauses = ["(path = ? OR name = ?)"]
    if not include_core:
        clauses.append("COALESCE(origin, 'user') != 'core'")
    row = conn.execute(
        """
        SELECT * FROM personal_scripts
        WHERE """ + " AND ".join(clauses) + """
        ORDER BY path = ? DESC
        LIMIT 1
        """,
        (normalized_path, name_or_path, normalized_path),
    ).fetchone()
    if not row:
        return None
    script = hydrate_personal_script(_row_to_dict(row))
    script["schedules"] = list_personal_script_schedules(script["id"])
    script["has_schedule"] = bool(script["schedules"])
    return script


def delete_personal_script(name_or_path: str) -> int:
    conn = _get_db()
    normalized_path = _normalize_script_path(name_or_path)
    result = conn.execute(
        "DELETE FROM personal_scripts WHERE path = ? OR name = ? OR id = ?",
        (normalized_path, name_or_path, name_or_path),
    )
    return int(result.rowcount or 0)


def record_personal_script_run(name_or_path: str, exit_code: int, run_at: str | None = None) -> None:
    conn = _get_db()
    run_at = run_at or _now_text()
    normalized_path = _normalize_script_path(name_or_path)
    conn.execute(
        """
        UPDATE personal_scripts
        SET last_run_at = ?, last_exit_code = ?, updated_at = ?
        WHERE path = ? OR name = ?
        """,
        (run_at, exit_code, _now_text(), normalized_path, name_or_path),
    )


def sync_personal_scripts_registry(
    script_records: list[dict],
    schedule_records: list[dict] | None = None,
    *,
    prune_missing: bool = True,
) -> dict:
    schedule_records = schedule_records or []
    conn = _get_db()
    repaired_paths: list[dict] = []
    active_paths: list[str] = []
    upserted = 0
    scheduled = 0

    rows = conn.execute("SELECT id, path FROM personal_scripts ORDER BY id ASC").fetchall()
    for row in rows:
        old_path = str(row["path"] or "")
        if not old_path:
            continue
        new_path = _normalize_script_path(old_path)
        if new_path == old_path:
            continue
        existing = conn.execute(
            "SELECT id FROM personal_scripts WHERE path = ? AND id != ? LIMIT 1",
            (new_path, row["id"]),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE personal_script_schedules SET script_id = ? WHERE script_id = ?",
                (existing["id"], row["id"]),
            )
            conn.execute("DELETE FROM personal_scripts WHERE id = ?", (row["id"],))
            repaired_paths.append({
                "id": row["id"],
                "old_path": old_path,
                "new_path": new_path,
                "deduped_into": existing["id"],
            })
            continue
        conn.execute(
            "UPDATE personal_scripts SET path = ?, updated_at = ? WHERE id = ?",
            (new_path, _now_text(), row["id"]),
        )
        repaired_paths.append({
            "id": row["id"],
            "old_path": old_path,
            "new_path": new_path,
        })

    for record in script_records:
        path = _normalize_script_path(record["path"])
        active_paths.append(path)
        upsert_personal_script(
            name=record.get("name") or Path(path).stem,
            path=path,
            description=record.get("description", ""),
            runtime=record.get("runtime", "unknown"),
            metadata=record.get("metadata", {}),
            created_by=record.get("created_by", "manual"),
            source=record.get("source", "filesystem"),
            origin=record.get("origin", "user"),
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
        "paths_repaired": repaired_paths,
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
