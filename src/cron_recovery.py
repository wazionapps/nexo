"""Shared cron recovery contract for catchup, launchagent sync, and diagnostics."""
from __future__ import annotations

import json
import os
import plistlib
import sqlite3
import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent)))
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
OPTIONALS_FILE = NEXO_HOME / "config" / "optionals.json"
DB_PATH = NEXO_HOME / "data" / "nexo.db"
STATE_FILE = NEXO_HOME / "operations" / ".catchup-state.json"


def _local_timezone():
    return datetime.now().astimezone().tzinfo or timezone.utc


def _load_json(path: Path, default):
    try:
        if path.is_file():
            return json.loads(path.read_text())
    except Exception:
        pass
    return default


def load_enabled_crons() -> list[dict]:
    manifest_candidates = [
        NEXO_HOME / "crons" / "manifest.json",
        NEXO_CODE / "crons" / "manifest.json",
    ]
    optionals = _load_json(OPTIONALS_FILE, {})
    if not isinstance(optionals, dict):
        optionals = {}

    for manifest_path in manifest_candidates:
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text())
        except Exception:
            continue

        enabled = []
        for cron in data.get("crons", []):
            optional_key = cron.get("optional")
            if optional_key and not optionals.get(optional_key, False):
                continue
            enabled.append(dict(cron))
        return enabled
    return []


def _calendar_payload_from_declared(value: str) -> dict | None:
    parts = str(value or "").split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        weekday = int(parts[2]) if len(parts) == 3 else None
    except ValueError:
        return None
    payload = {"hour": hour, "minute": minute}
    if weekday is not None:
        payload["weekday"] = weekday
    return payload


def load_managed_personal_crons() -> list[dict]:
    try:
        from script_registry import classify_scripts_dir, discover_personal_schedules
    except Exception:
        return []

    scripts_by_path: dict[str, dict] = {}
    for entry in classify_scripts_dir().get("entries", []):
        if entry.get("classification") != "personal":
            continue
        scripts_by_path[str(entry.get("path", ""))] = entry

    personal: list[dict] = []
    for schedule in discover_personal_schedules():
        script = scripts_by_path.get(str(schedule.get("script_path", "")))
        declared = (script or {}).get("declared_schedule", {})
        if not script or not declared.get("valid"):
            continue
        schedule_type = declared.get("schedule_type")
        if schedule_type not in {"calendar", "interval"}:
            continue
        personal.append({
            "id": schedule["cron_id"],
            "script": schedule["script_path"],
            "type": script.get("runtime", "python"),
            "schedule": declared.get("schedule", ""),
            "interval_seconds": int(declared.get("interval_seconds", 0) or 0),
            "schedule_type": schedule_type,
            "recovery_policy": declared.get("recovery_policy", "none"),
            "idempotent": bool(declared.get("idempotent", False)),
            "max_catchup_age": int(declared.get("max_catchup_age", 0) or 0),
            "run_on_boot": bool(declared.get("run_on_boot", False)),
            "run_on_wake": bool(declared.get("run_on_wake", False)),
            "personal_managed": True,
        })
    return personal


def default_recovery_policy(cron: dict) -> str:
    if cron.get("keep_alive") or cron.get("interval_seconds"):
        return "restart"
    if cron.get("schedule"):
        return "catchup"
    return "none"


def default_max_catchup_age(cron: dict) -> int:
    if cron.get("interval_seconds"):
        interval = int(cron["interval_seconds"])
        return max(interval * 4, interval + 900)
    schedule = cron.get("schedule") or {}
    if "weekday" in schedule:
        return 14 * 86400
    if "hour" in schedule and "minute" in schedule:
        return 48 * 3600
    return 0


def recovery_contract(cron: dict) -> dict:
    policy = cron.get("recovery_policy") or default_recovery_policy(cron)
    return {
        "recovery_policy": policy,
        "idempotent": bool(cron.get("idempotent", policy in {"catchup", "restart"})),
        "max_catchup_age": int(cron.get("max_catchup_age", default_max_catchup_age(cron)) or 0),
        "run_on_boot": bool(cron.get("run_on_boot", cron.get("run_at_load") or bool(cron.get("interval_seconds")))),
        "run_on_wake": bool(cron.get("run_on_wake", policy == "catchup" or bool(cron.get("interval_seconds")))),
    }


def should_run_at_load(cron: dict) -> bool:
    if cron.get("keep_alive"):
        return True
    if cron.get("run_at_load"):
        return True
    return bool(cron.get("run_on_boot") and cron.get("interval_seconds"))


def launchagent_schedule(cron_id: str) -> dict:
    plist_path = LAUNCH_AGENTS_DIR / f"com.nexo.{cron_id}.plist"
    if not plist_path.is_file():
        return {}
    try:
        with plist_path.open("rb") as fh:
            plist_data = plistlib.load(fh)
    except Exception:
        return {}

    result = {
        "source": "launchagent",
        "run_at_load": bool(plist_data.get("RunAtLoad")),
    }
    if "StartInterval" in plist_data:
        result["schedule_type"] = "interval"
        result["interval_seconds"] = int(plist_data["StartInterval"])
        return result
    if "StartCalendarInterval" in plist_data:
        result["schedule_type"] = "calendar"
        result["calendar"] = plist_data["StartCalendarInterval"]
        return result
    return result


def effective_schedule(cron: dict) -> dict:
    if cron.get("personal_managed"):
        if cron.get("schedule_type") == "interval":
            return {
                "source": "personal",
                "schedule_type": "interval",
                "interval_seconds": int(cron.get("interval_seconds", 0) or 0),
                "run_at_load": bool(cron.get("run_on_boot")),
            }
        if cron.get("schedule_type") == "calendar":
            calendar = _calendar_payload_from_declared(str(cron.get("schedule", ""))) or {}
            return {
                "source": "personal",
                "schedule_type": "calendar",
                "calendar": calendar,
                "run_at_load": bool(cron.get("run_on_boot")),
            }

    actual = launchagent_schedule(cron["id"])
    if actual.get("schedule_type"):
        return actual

    if cron.get("interval_seconds"):
        return {
            "source": "manifest",
            "schedule_type": "interval",
            "interval_seconds": int(cron["interval_seconds"]),
            "run_at_load": should_run_at_load(cron),
        }
    if cron.get("schedule"):
        return {
            "source": "manifest",
            "schedule_type": "calendar",
            "calendar": cron["schedule"],
            "run_at_load": should_run_at_load(cron),
        }
    return {
        "source": "manifest",
        "schedule_type": "manual",
        "run_at_load": should_run_at_load(cron),
    }


def _parse_timestamp(value: str, *, assume_utc: bool) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc if assume_utc else _local_timezone())
    return parsed


def latest_successful_runs(cron_ids: list[str], *, db_path: Path = DB_PATH) -> dict[str, datetime]:
    if not cron_ids or not db_path.is_file():
        return {}
    conn = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in cron_ids)
        rows = conn.execute(
            f"""
            SELECT c1.cron_id, c1.started_at
            FROM cron_runs c1
            JOIN (
                SELECT cron_id, MAX(id) AS max_id
                FROM cron_runs
                WHERE cron_id IN ({placeholders}) AND exit_code = 0
                GROUP BY cron_id
            ) latest ON latest.max_id = c1.id
            """,
            tuple(cron_ids),
        ).fetchall()
    except Exception:
        return {}
    finally:
        with contextlib.suppress(Exception):
            conn.close()

    result: dict[str, datetime] = {}
    for row in rows:
        parsed = _parse_timestamp(row["started_at"], assume_utc=True)
        if parsed is not None:
            result[row["cron_id"]] = parsed
    return result


def active_started_runs(cron_ids: list[str], *, db_path: Path = DB_PATH) -> dict[str, datetime]:
    """Return currently open cron_runs keyed by cron_id.

    A run is considered active if it has started but not yet recorded a final
    exit/ended timestamp. Catchup uses this to avoid relaunching the same cron
    window while another invocation is already in flight.
    """
    if not cron_ids or not db_path.is_file():
        return {}
    conn = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in cron_ids)
        rows = conn.execute(
            f"""
            SELECT c1.cron_id, c1.started_at
            FROM cron_runs c1
            JOIN (
                SELECT cron_id, MAX(id) AS max_id
                FROM cron_runs
                WHERE cron_id IN ({placeholders})
                  AND (exit_code IS NULL OR ended_at IS NULL)
                GROUP BY cron_id
            ) latest ON latest.max_id = c1.id
            """,
            tuple(cron_ids),
        ).fetchall()
    except Exception:
        return {}
    finally:
        with contextlib.suppress(Exception):
            conn.close()

    result: dict[str, datetime] = {}
    for row in rows:
        parsed = _parse_timestamp(row["started_at"], assume_utc=True)
        if parsed is not None:
            result[row["cron_id"]] = parsed
    return result


def legacy_state_runs(*, state_file: Path = STATE_FILE) -> dict[str, datetime]:
    state = _load_json(state_file, {})
    if not isinstance(state, dict):
        return {}
    parsed: dict[str, datetime] = {}
    for cron_id, value in state.items():
        timestamp = _parse_timestamp(str(value), assume_utc=False)
        if timestamp is not None:
            parsed[str(cron_id)] = timestamp
    return parsed


def last_scheduled_time(calendar: dict, now: datetime | None = None) -> datetime:
    now = now or datetime.now().astimezone(_local_timezone())
    if now.tzinfo is None:
        now = now.replace(tzinfo=_local_timezone())

    hour = int(calendar.get("hour", calendar.get("Hour", 0)))
    minute = int(calendar.get("minute", calendar.get("Minute", 0)))
    weekday = calendar.get("weekday", calendar.get("Weekday"))

    today_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if weekday is not None:
        py_weekday = (int(weekday) - 1) % 7
        days_since = (now.weekday() - py_weekday) % 7
        target = now - timedelta(days=days_since)
        target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target > now:
            target -= timedelta(weeks=1)
        return target
    if today_at <= now:
        return today_at
    return today_at - timedelta(days=1)


def catchup_candidates(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now().astimezone(_local_timezone())
    if now.tzinfo is None:
        now = now.replace(tzinfo=_local_timezone())

    crons = load_enabled_crons() + load_managed_personal_crons()
    contracts = {cron["id"]: recovery_contract(cron) for cron in crons if cron.get("id")}
    successes = latest_successful_runs(list(contracts), db_path=DB_PATH)
    active_runs = active_started_runs(list(contracts), db_path=DB_PATH)
    legacy = legacy_state_runs(state_file=STATE_FILE)
    candidates: list[dict] = []

    for cron in crons:
        cron_id = cron.get("id")
        if not cron_id or cron_id == "catchup":
            continue
        contract = contracts[cron_id]
        schedule = effective_schedule(cron)
        schedule_type = schedule.get("schedule_type")
        if schedule_type not in {"calendar", "interval"}:
            continue
        if not contract["idempotent"]:
            continue
        if schedule_type == "calendar":
            if contract["recovery_policy"] != "catchup":
                continue
            due_at = last_scheduled_time(schedule["calendar"], now)
        else:
            if contract["recovery_policy"] not in {"catchup", "run_once_on_wake"}:
                continue
            interval_seconds = int(schedule.get("interval_seconds", 0) or 0)
            if interval_seconds <= 0:
                continue
            due_at = now - timedelta(seconds=interval_seconds)
        last_success = successes.get(cron_id) or legacy.get(cron_id)
        active_started = active_runs.get(cron_id)
        age_seconds = max(int((now - due_at).total_seconds()), 0)
        is_inflight = active_started is not None and active_started >= due_at
        missed = (last_success is None or last_success < due_at) and not is_inflight
        within_window = contract["max_catchup_age"] <= 0 or age_seconds <= contract["max_catchup_age"]

        candidates.append({
            "cron_id": cron_id,
            "script": cron.get("script", ""),
            "type": cron.get("type", "python"),
            "personal_managed": bool(cron.get("personal_managed")),
            "contract": contract,
            "schedule": schedule,
            "last_due_at": due_at,
            "last_success_at": last_success,
            "active_started_at": active_started,
            "age_seconds": age_seconds,
            "missed": missed,
            "inflight": is_inflight,
            "within_window": within_window,
        })

    candidates.sort(key=lambda item: item["last_due_at"])
    return candidates
