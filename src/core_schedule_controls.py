from __future__ import annotations

"""Operator-managed cadence overrides for non-toggleable core crons."""

import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CORE_SCHEDULE_OVERRIDES_FILENAME = "schedule-overrides.json"
_TOGGLEABLE_AUTOMATIONS = frozenset({
    "email-monitor",
    "followup-runner",
    "morning-agent",
})
_EXCLUDED_HELPERS = frozenset({
    "prevent-sleep",
    "tcc-approve",
})
_NON_EDITABLE_REASONS: dict[str, str] = {
    "catchup": "Runs only at login/wake catch-up; cadence is fixed by product design.",
    "dashboard": "Persistent KeepAlive surface; cadence does not apply.",
}
_CLI_ONLY_REASONS: dict[str, str] = {
    "evolution": "Weekly support-ticket-only improvement cycle; adjust cadence from the CLI when needed.",
}
_INTERVAL_BOUNDS: dict[str, dict[str, int]] = {
    "auto-close-sessions": {
        "minimum_interval_seconds": 5 * 60,
        "maximum_interval_seconds": 60 * 60,
        "interval_step_seconds": 5 * 60,
    },
    "watchdog": {
        "minimum_interval_seconds": 10 * 60,
        "maximum_interval_seconds": 4 * 60 * 60,
        "interval_step_seconds": 10 * 60,
    },
    "immune": {
        "minimum_interval_seconds": 30 * 60,
        "maximum_interval_seconds": 12 * 60 * 60,
        "interval_step_seconds": 30 * 60,
    },
    "backup": {
        "minimum_interval_seconds": 30 * 60,
        "maximum_interval_seconds": 24 * 60 * 60,
        "interval_step_seconds": 30 * 60,
    },
    "cortex-cycle": {
        "minimum_interval_seconds": 60 * 60,
        "maximum_interval_seconds": 24 * 60 * 60,
        "interval_step_seconds": 60 * 60,
    },
}


def _normalize_name(name: str) -> str:
    return str(name or "").strip()


def _overrides_path() -> Path:
    try:
        from paths import personal_config_dir

        return personal_config_dir() / CORE_SCHEDULE_OVERRIDES_FILENAME
    except Exception:
        home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
        return home / "personal" / "config" / CORE_SCHEDULE_OVERRIDES_FILENAME


def _load_manifest_crons() -> list[dict[str, Any]]:
    try:
        from automation_controls import load_core_manifest_crons

        return load_core_manifest_crons()
    except Exception:
        return []


def _core_policy(name: str) -> dict[str, Any]:
    clean_name = _normalize_name(name)
    policy: dict[str, Any] = {}
    if clean_name in _INTERVAL_BOUNDS:
        policy.update(_INTERVAL_BOUNDS[clean_name])
    if clean_name in _NON_EDITABLE_REASONS:
        policy.update({
            "desktop_editable": False,
            "cli_editable": False,
            "note": _NON_EDITABLE_REASONS[clean_name],
        })
    elif clean_name in _CLI_ONLY_REASONS:
        policy.update({
            "desktop_editable": False,
            "cli_editable": True,
            "note": _CLI_ONLY_REASONS[clean_name],
        })
    else:
        policy.update({
            "desktop_editable": True,
            "cli_editable": True,
            "note": "",
        })
    return policy


def _is_core_schedule_cron(cron: dict[str, Any]) -> bool:
    if not isinstance(cron, dict):
        return False
    if not bool(cron.get("core")):
        return False
    name = _normalize_name(cron.get("id"))
    if not name or name in _TOGGLEABLE_AUTOMATIONS or name in _EXCLUDED_HELPERS:
        return False
    return True


def _parse_daily_at(value: str) -> dict[str, int] | None:
    raw = str(value or "").strip()
    if not raw or ":" not in raw:
        return None
    parts = raw.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except Exception:
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return {"hour": hour, "minute": minute}


def _format_calendar_label(schedule: dict[str, Any]) -> str:
    try:
        hour = int(schedule.get("hour", 0))
        minute = int(schedule.get("minute", 0))
    except Exception:
        return ""
    label = f"{hour:02d}:{minute:02d}"
    if "weekday" in schedule:
        weekdays = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
        try:
            label += f" {weekdays[int(schedule.get('weekday', 0)) % 7]}"
        except Exception:
            label += " weekly"
    else:
        label += " daily"
    return label


def _format_interval_label(interval_seconds: int) -> str:
    interval = max(1, int(interval_seconds or 0))
    if interval % 3600 == 0:
        return f"every {interval // 3600}h"
    if interval % 60 == 0:
        return f"every {interval // 60}m"
    return f"every {interval}s"


def _cron_schedule_label(cron: dict[str, Any]) -> str:
    if cron.get("interval_seconds"):
        return _format_interval_label(int(cron.get("interval_seconds", 0) or 0))
    schedule = cron.get("schedule")
    if isinstance(schedule, dict) and schedule:
        return _format_calendar_label(schedule)
    if cron.get("keep_alive"):
        return "persistent"
    if cron.get("run_at_load"):
        return "at login"
    return ""


def load_core_schedule_overrides() -> dict[str, dict[str, Any]]:
    path = _overrides_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_name, raw_override in payload.items():
        name = _normalize_name(raw_name)
        if not name or not isinstance(raw_override, dict):
            continue
        result[name] = dict(raw_override)
    return result


def _save_core_schedule_overrides(overrides: dict[str, dict[str, Any]]) -> Path:
    path = _overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not overrides:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass
        return path
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(overrides, indent=2, ensure_ascii=False) + "\n")
    tmp_path.replace(path)
    return path


def _audit_log_path() -> Path:
    home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    # Prefer the F0.6 runtime/logs location with a legacy fallback so audit
    # entries remain contiguous across installs that have not yet migrated.
    new = home / "runtime" / "logs" / "core-schedule-overrides.log"
    legacy = home / "logs" / "core-schedule-overrides.log"
    if new.parent.is_dir() or not legacy.parent.is_dir():
        return new
    return legacy


def _append_override_audit(
    *,
    name: str,
    action: str,
    previous: dict[str, Any],
    current: dict[str, Any],
    warning: str,
    actor: str,
) -> None:
    """Append a single-line JSON audit record for a schedule override change.

    Writes to ``~/.nexo/runtime/logs/core-schedule-overrides.log`` (or the
    legacy location on pre-F0.6 installs). Best-effort only: a failed log
    write never blocks the override itself.
    """
    try:
        log_path = _audit_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "name": name,
            "action": action,
            "previous": previous,
            "current": current,
            "warning": warning or "",
            "actor": actor or "cli",
        }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Audit logging is best-effort — never fail the operator action.
        pass


def _apply_calendar_override(base_cron: dict[str, Any], start_hour: str) -> dict[str, Any]:
    parsed = _parse_daily_at(start_hour)
    schedule = base_cron.get("schedule")
    if not parsed or not isinstance(schedule, dict) or not schedule:
        return dict(base_cron)
    effective = dict(base_cron)
    next_schedule = dict(schedule)
    next_schedule.update(parsed)
    effective["schedule"] = next_schedule
    effective.pop("interval_seconds", None)
    effective["_schedule_source"] = "override"
    return effective


def _clamp_interval(name: str, interval_seconds: int) -> tuple[int, str]:
    bounds = _core_policy(name)
    minimum = int(bounds.get("minimum_interval_seconds", 0) or 0)
    maximum = int(bounds.get("maximum_interval_seconds", 0) or 0)
    value = max(1, int(interval_seconds or 0))
    clamped = value
    if minimum:
        clamped = max(clamped, minimum)
    if maximum:
        clamped = min(clamped, maximum)
    if clamped == value:
        return clamped, ""
    return (
        clamped,
        (
            f"Requested {value}s for {name} is outside the safe range "
            f"{minimum or 1}s-{maximum or 'unbounded'}s. Applied {clamped}s instead."
        ),
    )


def apply_core_schedule_override(cron: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cron, dict):
        return {}
    if not _is_core_schedule_cron(cron):
        return dict(cron)
    name = _normalize_name(cron.get("id"))
    overrides = load_core_schedule_overrides()
    override = overrides.get(name) or {}
    effective = dict(cron)
    if not override:
        effective["_schedule_source"] = "manifest"
        return effective

    if "interval_seconds" in override:
        try:
            clamped, _warning = _clamp_interval(name, int(override.get("interval_seconds", 0) or 0))
        except Exception:
            clamped = 0
        if clamped > 0:
            effective["interval_seconds"] = clamped
            effective.pop("schedule", None)
            effective["_schedule_source"] = "override"
            return effective

    start_hour = str(override.get("start_hour") or "").strip()
    if start_hour:
        return _apply_calendar_override(cron, start_hour)

    effective["_schedule_source"] = "manifest"
    return effective


def apply_core_schedule_overrides(crons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [apply_core_schedule_override(cron) for cron in crons]


def _schedule_state_from_crons(*, base_cron: dict[str, Any], effective_cron: dict[str, Any]) -> dict[str, Any]:
    source = str(effective_cron.get("_schedule_source") or "manifest")
    policy = _core_policy(str(base_cron.get("id") or ""))
    if effective_cron.get("interval_seconds"):
        return {
            "schedule_type": "interval",
            "schedule_source": source,
            "effective_schedule_label": _cron_schedule_label(effective_cron),
            "default_schedule_label": _cron_schedule_label(base_cron),
            "interval_seconds": int(effective_cron.get("interval_seconds", 0) or 0),
            "default_interval_seconds": int(base_cron.get("interval_seconds", 0) or 0),
            "minimum_interval_seconds": int(policy.get("minimum_interval_seconds", 0) or 0),
            "maximum_interval_seconds": int(policy.get("maximum_interval_seconds", 0) or 0),
            "interval_step_seconds": int(policy.get("interval_step_seconds", 0) or 0),
            "schedule": None,
            "default_schedule": None,
        }
    schedule = effective_cron.get("schedule")
    base_schedule = base_cron.get("schedule")
    if isinstance(schedule, dict) and schedule:
        return {
            "schedule_type": "calendar",
            "schedule_source": source,
            "effective_schedule_label": _cron_schedule_label(effective_cron),
            "default_schedule_label": _cron_schedule_label(base_cron),
            "interval_seconds": 0,
            "default_interval_seconds": 0,
            "minimum_interval_seconds": 0,
            "maximum_interval_seconds": 0,
            "interval_step_seconds": 0,
            "schedule": dict(schedule),
            "default_schedule": dict(base_schedule) if isinstance(base_schedule, dict) else {},
        }
    return {
        "schedule_type": "manual",
        "schedule_source": source,
        "effective_schedule_label": _cron_schedule_label(effective_cron),
        "default_schedule_label": _cron_schedule_label(base_cron),
        "interval_seconds": 0,
        "default_interval_seconds": 0,
        "minimum_interval_seconds": 0,
        "maximum_interval_seconds": 0,
        "interval_step_seconds": 0,
        "schedule": None,
        "default_schedule": None,
    }


def get_core_schedule_state(name: str) -> dict[str, Any]:
    clean_name = _normalize_name(name)
    base_cron = next(
        (
            dict(cron)
            for cron in _load_manifest_crons()
            if _is_core_schedule_cron(cron) and _normalize_name(cron.get("id")) == clean_name
        ),
        {},
    )
    if not base_cron:
        return {"ok": False, "error": f"Core schedule not found: {clean_name or name}"}

    effective = apply_core_schedule_override(base_cron)
    state = _schedule_state_from_crons(base_cron=base_cron, effective_cron=effective)
    policy = _core_policy(clean_name)
    state.update({
        "ok": True,
        "name": clean_name,
        "description": str(base_cron.get("description") or ""),
        "desktop_editable": bool(policy.get("desktop_editable")),
        "cli_editable": bool(policy.get("cli_editable")),
        "note": str(policy.get("note") or ""),
        "config_path": str(_overrides_path()),
    })
    return state


def _latest_cron_run(name: str) -> dict[str, Any] | None:
    try:
        from db import init_db
        from db._core import get_db

        init_db()
        conn = get_db()
        row = conn.execute(
            "SELECT exit_code, started_at, ended_at, summary FROM cron_runs "
            "WHERE cron_id = ? ORDER BY id DESC LIMIT 1",
            (name,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def get_core_schedule_status(name: str) -> dict[str, Any]:
    state = get_core_schedule_state(name)
    if state.get("ok") is False:
        return state
    latest = _latest_cron_run(str(state.get("name") or ""))
    state.update({
        "last_run_at": latest.get("started_at") if latest else None,
        "last_exit_code": latest.get("exit_code") if latest else None,
        "last_summary": str(latest.get("summary") or "") if latest else "",
    })
    return state


def list_core_schedules() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cron in _load_manifest_crons():
        if not _is_core_schedule_cron(cron):
            continue
        row = get_core_schedule_status(str(cron.get("id") or ""))
        if row.get("ok") is False:
            continue
        rows.append(row)
    return rows


def _sync_core_crons_runtime() -> dict[str, Any]:
    try:
        from crons import sync as cron_sync

        if platform.system() == "Linux":
            cron_sync.sync_linux()
            return {"ok": True, "method": "systemd"}
        cron_sync.sync()
        return {"ok": True, "method": "launchagent"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def set_core_schedule(
    name: str,
    *,
    interval_seconds: int | None = None,
    daily_at: str | None = None,
    clear: bool = False,
    actor: str = "cli",
) -> dict[str, Any]:
    clean_name = _normalize_name(name)
    if clean_name in _TOGGLEABLE_AUTOMATIONS:
        return {
            "ok": False,
            "error": f"Use Preferences -> Automations to manage {clean_name}.",
        }

    state = get_core_schedule_state(clean_name)
    if state.get("ok") is False:
        return state

    if not state.get("cli_editable"):
        return {
            "ok": False,
            "error": str(state.get("note") or "This core schedule is not editable."),
            "name": clean_name,
        }

    overrides = load_core_schedule_overrides()
    previous_snapshot = dict(overrides.get(clean_name) or {})
    changed = False
    warning = ""
    if clear:
        changed = clean_name in overrides
        overrides.pop(clean_name, None)
    elif state.get("schedule_type") == "interval":
        try:
            requested = int(interval_seconds or 0)
        except Exception:
            return {"ok": False, "error": "interval_seconds must be an integer > 0", "name": clean_name}
        if requested <= 0:
            return {"ok": False, "error": "interval_seconds must be > 0", "name": clean_name}
        clamped, warning = _clamp_interval(clean_name, requested)
        default_interval = int(state.get("default_interval_seconds", 0) or 0)
        next_override = {} if clamped == default_interval else {"interval_seconds": clamped}
        previous_override = dict(overrides.get(clean_name) or {})
        changed = previous_override != next_override
        if next_override:
            overrides[clean_name] = next_override
        else:
            overrides.pop(clean_name, None)
    elif state.get("schedule_type") == "calendar":
        parsed_daily_at = _parse_daily_at(str(daily_at or "").strip())
        if not parsed_daily_at:
            return {"ok": False, "error": "daily_at must use HH:MM (24h) format", "name": clean_name}
        next_start_hour = f"{parsed_daily_at['hour']:02d}:{parsed_daily_at['minute']:02d}"
        default_schedule = state.get("default_schedule") or {}
        default_start_hour = ""
        if isinstance(default_schedule, dict) and default_schedule:
            default_start_hour = f"{int(default_schedule.get('hour', 0)):02d}:{int(default_schedule.get('minute', 0)):02d}"
        next_override = {} if next_start_hour == default_start_hour else {"start_hour": next_start_hour}
        previous_override = dict(overrides.get(clean_name) or {})
        changed = previous_override != next_override
        if next_override:
            overrides[clean_name] = next_override
        else:
            overrides.pop(clean_name, None)
    else:
        return {
            "ok": False,
            "error": str(state.get("note") or "This core schedule does not support cadence overrides."),
            "name": clean_name,
        }

    config_path = _save_core_schedule_overrides(overrides)

    if changed:
        current_snapshot = dict(overrides.get(clean_name) or {})
        if clear:
            audit_action = "clear"
        elif not previous_snapshot:
            audit_action = "set"
        else:
            audit_action = "update"
        _append_override_audit(
            name=clean_name,
            action=audit_action,
            previous=previous_snapshot,
            current=current_snapshot,
            warning=warning,
            actor=actor,
        )

    sync_result = _sync_core_crons_runtime()
    refreshed = get_core_schedule_status(clean_name)
    refreshed.update({
        "changed": changed,
        "config_path": str(config_path),
        "runtime_sync": sync_result,
    })
    if warning:
        refreshed["warning"] = warning
    return refreshed
