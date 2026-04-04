"""NEXO Script Registry — discovery, metadata, validation for personal scripts.

Scripts live in NEXO_HOME/scripts/. Core scripts (from manifest) are filtered by default.
Personal scripts use CLI as stable interface, never direct DB access.
"""
from __future__ import annotations

import contextlib
import json
import os
import platform
import plistlib
import re
import shutil
import stat
import subprocess
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent)))

# Internal artifacts to always ignore
_IGNORED_FILES = {
    ".watchdog-hashes",
    ".watchdog-fails",
    ".watchdog-nexo-repair.lock",
    "nexo-cron-wrapper.sh",
    "nexo-dashboard.sh",
    "nexo-prevent-sleep.sh",
    "nexo-proactive-dashboard.py",
    "nexo-tcc-approve.sh",
}
_IGNORED_DIRS = {"deep-sleep", "__pycache__"}

# Forbidden patterns — direct DB access from personal scripts
_FORBIDDEN_PATTERNS = [
    re.compile(r"\bsqlite3\b"),
    re.compile(r"\bnexo\.db\b"),
    re.compile(r"\bcognitive\.db\b"),
    re.compile(r"/data/nexo\.db"),
    re.compile(r"/data/cognitive\.db"),
    re.compile(r"\bimport\s+db\b"),
    re.compile(r"\bfrom\s+db\s+import\b"),
    re.compile(r"\bimport\s+cognitive\b"),
    re.compile(r"\bfrom\s+cognitive\s+import\b"),
]

METADATA_KEYS = {
    "name",
    "description",
    "runtime",
    "timeout",
    "requires",
    "tools",
    "hidden",
    "category",
    "cron_id",
    "schedule",
    "interval_seconds",
    "schedule_required",
    "recovery_policy",
    "run_on_boot",
    "run_on_wake",
    "idempotent",
    "max_catchup_age",
}
SUPPORTED_RUNTIMES = {"python", "shell", "node", "php", "unknown"}
PERSONAL_SCHEDULE_MANAGED_ENV = "NEXO_MANAGED_PERSONAL_CRON"
SUPPORTED_RECOVERY_POLICIES = {"none", "run_once_on_wake", "catchup", "restart", "restart_daemon"}


def get_nexo_home() -> Path:
    return NEXO_HOME


def get_scripts_dir() -> Path:
    return NEXO_HOME / "scripts"


def load_core_script_names() -> set[str]:
    """Load script names from crons/manifest.json (these are core, not personal)."""
    names: set[str] = set()
    for manifest_path in [NEXO_CODE / "crons" / "manifest.json", NEXO_HOME / "crons" / "manifest.json"]:
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text())
                for cron in data.get("crons", []):
                    script = cron.get("script", "")
                    # script is like "scripts/nexo-immune.py" — extract filename
                    names.add(Path(script).name)
                break
            except Exception:
                continue
    return names


def parse_inline_metadata(path: Path) -> dict:
    """Parse inline metadata from first 25 lines.

    Supported comment prefixes:
    - # nexo:
    - // nexo:
    """
    meta: dict[str, str] = {}
    try:
        lines = path.read_text(errors="ignore").splitlines()[:25]
    except Exception:
        return meta

    for line in lines:
        stripped = line.strip()
        payload = ""
        if stripped.startswith("# nexo:"):
            payload = stripped[len("# nexo:"):].strip()
        elif stripped.startswith("// nexo:"):
            payload = stripped[len("// nexo:"):].strip()
        else:
            continue
        if "=" not in payload:
            continue
        key, value = payload.split("=", 1)
        k = key.strip()
        if k in METADATA_KEYS:
            meta[k] = value.strip()
    return meta


def _detect_shebang(path: Path) -> str | None:
    """Read first line for shebang."""
    try:
        first = path.read_text(errors="ignore").split("\n", 1)[0]
        if first.startswith("#!"):
            return first
    except Exception:
        pass
    return None


def classify_runtime(path: Path, metadata: dict) -> str:
    """Detect script runtime: python, shell, node, php, or unknown."""
    # 1. Metadata
    rt = metadata.get("runtime", "").lower()
    if rt in ("python", "shell", "node", "php"):
        return rt

    # 2. Shebang
    shebang = _detect_shebang(path)
    if shebang:
        if "python" in shebang:
            return "python"
        if "bash" in shebang or "/sh" in shebang:
            return "shell"
        if "node" in shebang:
            return "node"
        if "php" in shebang:
            return "php"

    # 3. Extension
    ext = path.suffix.lower()
    if ext == ".py":
        return "python"
    if ext == ".sh":
        return "shell"
    if ext == ".js":
        return "node"
    if ext == ".php":
        return "php"

    return "unknown"


def _is_ignored(path: Path) -> bool:
    """Check if file should be ignored entirely."""
    if path.name in _IGNORED_FILES:
        return True
    if path.name.startswith("."):
        return True
    for parent in path.relative_to(get_scripts_dir()).parents:
        if parent.name in _IGNORED_DIRS:
            return True
    return False


def _is_script_candidate(path: Path, metadata: dict | None = None) -> bool:
    metadata = metadata or {}
    runtime = classify_runtime(path, metadata)
    if runtime != "unknown":
        return True
    if _detect_shebang(path):
        return True
    try:
        return os.access(path, os.X_OK)
    except Exception:
        return False


def _truthy(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_slug(value: str) -> str:
    chars: list[str] = []
    for ch in value.lower():
        if ch.isalnum():
            chars.append(ch)
        elif ch in {"-", "_", " "}:
            chars.append("-")
    slug = "".join(chars).strip("-")
    return slug or "script"


def get_declared_schedule(metadata: dict, default_name: str = "") -> dict:
    """Parse desired schedule metadata from inline script metadata."""
    explicit_name = metadata.get("name", "").strip()
    explicit_runtime = metadata.get("runtime", "").strip().lower()
    explicit_cron_id = metadata.get("cron_id", "").strip()
    cron_id = explicit_cron_id or _safe_slug(default_name or explicit_name or "script")
    interval_raw = metadata.get("interval_seconds", "").strip()
    schedule_raw = metadata.get("schedule", "").strip()
    schedule_required = _truthy(metadata.get("schedule_required"))
    recovery_policy_raw = metadata.get("recovery_policy", "").strip().lower()
    run_on_boot = _truthy(metadata.get("run_on_boot"))
    run_on_wake = _truthy(metadata.get("run_on_wake"))
    idempotent = _truthy(metadata.get("idempotent"))
    max_catchup_age_raw = metadata.get("max_catchup_age", "").strip()
    required = schedule_required or bool(interval_raw or schedule_raw)

    if recovery_policy_raw and recovery_policy_raw not in SUPPORTED_RECOVERY_POLICIES:
        return {
            "required": required,
            "valid": False,
            "error": f"Invalid recovery_policy: {recovery_policy_raw}",
            "cron_id": cron_id,
        }

    max_catchup_age = 0
    if max_catchup_age_raw:
        try:
            max_catchup_age = int(max_catchup_age_raw)
        except ValueError:
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid max_catchup_age: {max_catchup_age_raw}",
                "cron_id": cron_id,
            }
        if max_catchup_age < 0:
            return {
                "required": required,
                "valid": False,
                "error": f"max_catchup_age must be >= 0 (got {max_catchup_age_raw})",
                "cron_id": cron_id,
            }

    if required:
        missing = []
        if not explicit_name:
            missing.append("name")
        if not explicit_runtime:
            missing.append("runtime")
        elif explicit_runtime not in SUPPORTED_RUNTIMES - {"unknown"}:
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid runtime metadata for scheduled script: {explicit_runtime}",
                "cron_id": cron_id,
            }
        if not explicit_cron_id:
            missing.append("cron_id")
        if not schedule_required:
            missing.append("schedule_required=true")
        if missing:
            return {
                "required": required,
                "valid": False,
                "error": f"Scheduled scripts must declare {', '.join(missing)}",
                "cron_id": cron_id,
            }

    def _effective_run_on_wake(policy: str) -> bool:
        if "run_on_wake" in metadata:
            return run_on_wake
        return policy in {"catchup", "run_once_on_wake"}

    def _effective_idempotent(policy: str) -> bool:
        if "idempotent" in metadata:
            return idempotent
        return policy in {"catchup", "run_once_on_wake", "restart", "restart_daemon"}

    if interval_raw and schedule_raw:
        return {
            "required": required,
            "valid": False,
            "error": "Both schedule and interval_seconds are set; choose one.",
            "cron_id": cron_id,
        }

    if interval_raw:
        try:
            interval = int(interval_raw)
        except ValueError:
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid interval_seconds: {interval_raw}",
                "cron_id": cron_id,
            }
        if interval <= 0:
            return {
                "required": required,
                "valid": False,
                "error": f"interval_seconds must be > 0 (got {interval_raw})",
                "cron_id": cron_id,
            }
        return {
            "required": required,
            "valid": True,
            "cron_id": cron_id,
            "schedule_type": "interval",
            "schedule_value": str(interval),
            "schedule_label": f"every {interval}s",
            "schedule": "",
            "interval_seconds": interval,
            "recovery_policy": recovery_policy_raw or "run_once_on_wake",
            "run_on_boot": run_on_boot,
            "run_on_wake": _effective_run_on_wake(recovery_policy_raw or "run_once_on_wake"),
            "idempotent": _effective_idempotent(recovery_policy_raw or "run_once_on_wake"),
            "max_catchup_age": max_catchup_age or max(interval * 4, interval + 900),
        }

    if schedule_raw:
        parts = schedule_raw.split(":")
        if len(parts) not in {2, 3}:
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid schedule format: {schedule_raw}",
                "cron_id": cron_id,
            }
        try:
            hour = int(parts[0])
            minute = int(parts[1])
            weekday = int(parts[2]) if len(parts) == 3 else None
        except ValueError:
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid schedule format: {schedule_raw}",
                "cron_id": cron_id,
            }
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid schedule time: {schedule_raw}",
                "cron_id": cron_id,
            }
        if weekday is not None and not (0 <= weekday <= 6):
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid schedule weekday: {schedule_raw}",
                "cron_id": cron_id,
            }
        label = f"{hour:02d}:{minute:02d}"
        if weekday is not None:
            label += f" weekday={weekday}"
        else:
            label += " daily"
        return {
            "required": required,
            "valid": True,
            "cron_id": cron_id,
            "schedule_type": "calendar",
            "schedule_value": schedule_raw,
            "schedule_label": label,
            "schedule": schedule_raw,
            "interval_seconds": 0,
            "recovery_policy": recovery_policy_raw or "catchup",
            "run_on_boot": run_on_boot,
            "run_on_wake": _effective_run_on_wake(recovery_policy_raw or "catchup"),
            "idempotent": _effective_idempotent(recovery_policy_raw or "catchup"),
            "max_catchup_age": max_catchup_age or (14 * 86400 if weekday is not None else 48 * 3600),
        }

    return {
        "required": required,
        "valid": not required,
        "error": "" if not required else "schedule_required=true but no schedule metadata was provided.",
        "cron_id": cron_id,
        "recovery_policy": recovery_policy_raw or "none",
        "run_on_boot": run_on_boot,
        "run_on_wake": run_on_wake,
        "idempotent": idempotent,
        "max_catchup_age": max_catchup_age,
    }


def _script_entry(path: Path, meta: dict, *, is_core: bool, classification: str, reason: str = "") -> dict:
    runtime = classify_runtime(path, meta)
    name = meta.get("name", path.stem)
    return {
        "name": name,
        "runtime": runtime,
        "description": meta.get("description", ""),
        "path": str(path),
        "core": is_core,
        "metadata": meta,
        "classification": classification,
        "reason": reason,
        "declared_schedule": get_declared_schedule(meta, name),
    }


def classify_scripts_dir() -> dict:
    """Classify every file in NEXO_HOME/scripts into personal/core/ignored/non-script buckets."""
    scripts_dir = get_scripts_dir()
    if not scripts_dir.is_dir():
        return {"scripts_dir": str(scripts_dir), "entries": [], "summary": {}}

    core_names = load_core_script_names()
    entries: list[dict] = []
    for f in sorted(scripts_dir.iterdir()):
        if not f.is_file():
            continue

        meta = parse_inline_metadata(f)
        if _is_ignored(f):
            entries.append(_script_entry(f, meta, is_core=False, classification="ignored", reason="internal or hidden artifact"))
            continue

        if not _is_script_candidate(f, meta):
            entries.append(_script_entry(f, meta, is_core=False, classification="non-script", reason="not an executable/script candidate"))
            continue

        is_core = f.name in core_names
        classification = "core" if is_core else "personal"
        entries.append(_script_entry(f, meta, is_core=is_core, classification=classification))

    summary: dict[str, int] = {}
    for entry in entries:
        summary[entry["classification"]] = summary.get(entry["classification"], 0) + 1
    return {"scripts_dir": str(scripts_dir), "entries": entries, "summary": summary}


def list_scripts(include_core: bool = False) -> list[dict]:
    """List scripts in NEXO_HOME/scripts/.

    By default only personal scripts. With include_core=True, also shows core/cron scripts.
    """
    results = []
    for entry in classify_scripts_dir()["entries"]:
        if entry["classification"] not in {"personal", "core"}:
            continue
        if entry["core"] and not include_core:
            continue
        hidden = _truthy(entry.get("metadata", {}).get("hidden"))
        if hidden and not include_core:
            continue
        results.append(entry)
    return results


def _within_scripts_dir(path: Path) -> bool:
    try:
        path.resolve().relative_to(get_scripts_dir().resolve())
        return True
    except Exception:
        return False


def resolve_script(name: str) -> dict | None:
    """Find a script by name (metadata name or filename stem)."""
    scripts_dir = get_scripts_dir()
    if not scripts_dir.is_dir():
        return None

    for f in scripts_dir.iterdir():
        if not f.is_file() or _is_ignored(f):
            continue
        meta = parse_inline_metadata(f)
        if not _is_script_candidate(f, meta):
            continue
        script_name = meta.get("name", f.stem)
        if script_name == name or f.stem == name:
            runtime = classify_runtime(f, meta)
            return {
                "name": script_name,
                "runtime": runtime,
                "description": meta.get("description", ""),
                "path": str(f),
                "core": f.name in load_core_script_names(),
                "metadata": meta,
            }
    return None


def resolve_script_reference(ref: str) -> dict | None:
    """Resolve a script by name or by direct filesystem path."""
    direct = Path(ref)
    if direct.is_file():
        meta = parse_inline_metadata(direct)
        return {
            "name": meta.get("name", direct.stem),
            "runtime": classify_runtime(direct, meta),
            "description": meta.get("description", ""),
            "path": str(direct),
            "core": direct.name in load_core_script_names(),
            "metadata": meta,
        }
    return resolve_script(ref)


def _extract_script_path_from_program_args(program_args: list) -> Path | None:
    candidate = _extract_script_path_candidate(program_args)
    if candidate is None:
        return None
    if not candidate.is_file():
        return None
    if not _within_scripts_dir(candidate):
        return None
    if _is_ignored(candidate):
        return None
    return candidate


def _extract_script_path_candidate(program_args: list) -> Path | None:
    candidates: list[Path] = []
    for arg in program_args or []:
        if not isinstance(arg, str):
            continue
        candidate = Path(arg).expanduser()
        if not str(candidate).startswith("/") and not str(arg).startswith("~"):
            continue
        candidates.append(candidate)
    if not candidates:
        return None
    return candidates[-1]


def _format_schedule_from_plist(plist_data: dict) -> tuple[str, str, str]:
    if plist_data.get("KeepAlive") is True:
        return "keep_alive", "true", "keep alive"
    if plist_data.get("RunAtLoad") is True and "StartInterval" not in plist_data and "StartCalendarInterval" not in plist_data:
        return "run_at_load", "true", "run at load"

    if "StartInterval" in plist_data:
        interval = int(plist_data["StartInterval"])
        return "interval", str(interval), f"every {interval}s"

    cal = plist_data.get("StartCalendarInterval")
    if cal:
        if isinstance(cal, list):
            value = json.dumps(cal, ensure_ascii=False)
            return "calendar", value, "calendar"
        hour = cal.get("Hour")
        minute = cal.get("Minute")
        weekday = cal.get("Weekday")
        if weekday is not None and hour is not None and minute is not None:
            return "calendar", json.dumps(cal, ensure_ascii=False), f"{hour:02d}:{minute:02d} weekday={weekday}"
        if hour is not None and minute is not None:
            return "calendar", json.dumps(cal, ensure_ascii=False), f"{hour:02d}:{minute:02d} daily"
        return "calendar", json.dumps(cal, ensure_ascii=False), "calendar"

    return "manual", "", ""


def _calendar_payload_from_declared(schedule_value: str) -> dict | list | None:
    if not schedule_value:
        return None
    if schedule_value.lstrip().startswith("{") or schedule_value.lstrip().startswith("["):
        try:
            parsed = json.loads(schedule_value)
        except Exception:
            return None
        return parsed if isinstance(parsed, (dict, list)) else None

    parts = schedule_value.split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        weekday = int(parts[2]) if len(parts) == 3 else None
    except ValueError:
        return None

    payload = {"Hour": hour, "Minute": minute}
    if weekday is not None:
        payload["Weekday"] = weekday
    return payload


def _canonical_schedule_value(schedule_type: str, schedule_value: str | dict | list) -> str:
    if schedule_type == "calendar":
        payload = _calendar_payload_from_declared(str(schedule_value)) if isinstance(schedule_value, str) else schedule_value
        if payload is None:
            return str(schedule_value or "")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return str(schedule_value or "")


def _discover_personal_schedule_records() -> list[dict]:
    """Inspect macOS LaunchAgents and return raw personal schedule records."""
    if platform.system() != "Darwin":
        return []

    results = []
    launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
    if not launch_agents_dir.is_dir():
        return results

    core_names = load_core_script_names()
    for plist_path in sorted(launch_agents_dir.glob("com.nexo.*.plist")):
        try:
            with plist_path.open("rb") as fh:
                plist_data = plistlib.load(fh)
        except Exception:
            continue

        env = plist_data.get("EnvironmentVariables") or {}
        if env.get("NEXO_MANAGED_CORE_CRON") == "1":
            continue

        program_args = plist_data.get("ProgramArguments") or []
        candidate = _extract_script_path_candidate(program_args)
        label = str(plist_data.get("Label", plist_path.stem))
        cron_id = label.replace("com.nexo.", "", 1)
        script_path = candidate.expanduser() if candidate is not None else None
        in_scripts_dir = bool(script_path and _within_scripts_dir(script_path))
        exists = bool(script_path and script_path.is_file())
        ignored = bool(script_path and in_scripts_dir and _is_ignored(script_path))
        is_core = bool(script_path and exists and script_path.name in core_names)
        if is_core or ignored:
            continue

        schedule_type, schedule_value, schedule_label = _format_schedule_from_plist(plist_data)
        results.append({
            "cron_id": cron_id,
            "script_path": str(script_path) if script_path else "",
            "schedule_type": schedule_type,
            "schedule_value": schedule_value,
            "schedule_label": schedule_label,
            "run_at_load": bool(plist_data.get("RunAtLoad")),
            "launchd_label": label,
            "plist_path": str(plist_path),
            "enabled": True,
            "description": "",
            "managed_marker": env.get(PERSONAL_SCHEDULE_MANAGED_ENV) == "1",
            "script_exists": exists,
            "script_within_scripts_dir": in_scripts_dir,
        })

    return results


def audit_personal_schedules() -> dict:
    """Return semantic schedule audit for personal LaunchAgents.

    Only schedules created/repaired through the official flow count as managed.
    Manual plists are discovered for visibility and repair, but never blessed.
    """
    classification = classify_scripts_dir()
    personal_scripts = [entry for entry in classification["entries"] if entry["classification"] == "personal"]
    scripts_by_path = {
        str(Path(entry["path"]).expanduser().resolve(strict=False)): entry
        for entry in personal_scripts
    }

    audited: list[dict] = []
    summary = {
        "declared_managed": 0,
        "discovered_manual": 0,
        "orphan_schedule": 0,
        "healthy": 0,
        "problems": 0,
        "managed_registered": 0,
    }

    for record in _discover_personal_schedule_records():
        script_path = record.get("script_path", "")
        resolved_path = str(Path(script_path).expanduser().resolve(strict=False)) if script_path else ""
        script = scripts_by_path.get(resolved_path)
        declared = script.get("declared_schedule", {}) if script else {}
        declared_valid = bool(script and declared.get("required") and declared.get("valid"))
        matches = declared_valid and _schedule_matches(record, declared)

        if record.get("managed_marker") and declared_valid:
            schedule_origin = "declared_managed"
        elif declared_valid:
            schedule_origin = "discovered_manual"
        else:
            schedule_origin = "orphan_schedule"

        problems: list[str] = []
        if not record.get("script_within_scripts_dir"):
            problems.append("schedule points outside NEXO_HOME/scripts")
        elif not record.get("script_path"):
            problems.append("schedule does not resolve a script path")
        elif not record.get("script_exists"):
            problems.append(f"scheduled script missing: {record['script_path']}")
        elif not script:
            problems.append("schedule points to a script that is not a registered personal script")

        if script and not declared.get("required"):
            problems.append("personal schedule exists without declared inline metadata")
        elif script and declared.get("required") and not declared.get("valid"):
            problems.append(declared.get("error", "invalid declared schedule metadata"))
        elif declared_valid and not matches:
            problems.append(
                f"schedule drift: actual {record.get('schedule_label') or record.get('schedule_value') or record.get('schedule_type')} "
                f"!= declared {declared.get('schedule_label') or declared.get('cron_id')}"
            )

        if declared_valid and not record.get("managed_marker"):
            problems.append("schedule was discovered manually and must be recreated via nexo scripts reconcile")

        schedule_managed = bool(schedule_origin == "declared_managed" and matches and not problems)
        if schedule_managed:
            schedule_state = "healthy"
        elif schedule_origin == "declared_managed":
            schedule_state = "drifted"
        elif schedule_origin == "discovered_manual" and matches:
            schedule_state = "manual_matching_declared"
        elif schedule_origin == "discovered_manual":
            schedule_state = "manual_drift"
        else:
            schedule_state = "orphaned"

        audited_record = dict(record)
        audited_record.update({
            "schedule_origin": schedule_origin,
            "schedule_declared": declared_valid,
            "schedule_managed": schedule_managed,
            "schedule_matches_declared": matches,
            "schedule_state": schedule_state,
            "problems": problems,
            "script_name": script.get("name", "") if script else "",
            "declared_schedule": declared if script else {},
        })
        audited.append(audited_record)
        summary[schedule_origin] += 1
        if schedule_managed:
            summary["healthy"] += 1
            summary["managed_registered"] += 1
        else:
            summary["problems"] += 1

    return {
        "schedules": audited,
        "summary": summary,
    }


def discover_personal_schedules() -> list[dict]:
    """Return only healthy managed personal schedules."""
    managed: list[dict] = []
    for record in audit_personal_schedules()["schedules"]:
        if record.get("schedule_managed"):
            managed.append({
                "cron_id": record["cron_id"],
                "script_path": record["script_path"],
                "schedule_type": record["schedule_type"],
                "schedule_value": record["schedule_value"],
                "schedule_label": record["schedule_label"],
                "launchd_label": record["launchd_label"],
                "plist_path": record["plist_path"],
                "enabled": record.get("enabled", True),
                "description": record.get("description", ""),
            })
    return managed


def sync_personal_scripts(prune_missing: bool = True) -> dict:
    """Sync filesystem + scheduler state into the DB-backed personal scripts registry."""
    from db import init_db, sync_personal_scripts_registry

    init_db()
    classification = classify_scripts_dir()
    scripts = [entry for entry in classification["entries"] if entry["classification"] == "personal"]
    schedule_audit = audit_personal_schedules()
    schedules = [record for record in schedule_audit["schedules"] if record.get("schedule_managed")]
    result = sync_personal_scripts_registry(scripts, schedules, prune_missing=prune_missing)
    result["classification"] = classification["summary"]
    missing_declared = []
    managed_by_path: dict[str, list[dict]] = {}
    for schedule in schedules:
        managed_by_path.setdefault(schedule["script_path"], []).append(schedule)
    schedules_by_path: dict[str, list[dict]] = {}
    for schedule in schedule_audit["schedules"]:
        schedules_by_path.setdefault(schedule["script_path"], []).append(schedule)
    for script in scripts:
        declared = script.get("declared_schedule", {})
        if not declared.get("required"):
            continue
        healthy = managed_by_path.get(script["path"], [])
        if healthy:
            continue
        attached = schedules_by_path.get(script["path"], [])
        if not attached:
            missing_declared.append({
                "name": script["name"],
                "path": script["path"],
                "declared_schedule": declared,
                "reason": "no schedule discovered",
            })
            continue
        attached_states = [item.get("schedule_state", item.get("schedule_origin", "unknown")) for item in attached]
        missing_declared.append({
            "name": script["name"],
            "path": script["path"],
            "declared_schedule": declared,
            "reason": f"schedule discovered but not managed ({', '.join(attached_states)})",
        })
    result["schedule_audit"] = schedule_audit
    result["missing_declared_schedules"] = missing_declared
    return result


def _schedule_matches(existing: dict, declared: dict) -> bool:
    if not existing or not declared.get("valid"):
        return False
    if existing.get("cron_id") != declared.get("cron_id"):
        return False
    if existing.get("schedule_type") != declared.get("schedule_type"):
        return False
    existing_value = _canonical_schedule_value(existing.get("schedule_type", ""), existing.get("schedule_value", ""))
    declared_value = _canonical_schedule_value(declared.get("schedule_type", ""), declared.get("schedule_value", ""))
    if existing_value != declared_value:
        return False
    if bool(existing.get("run_at_load")) != bool(declared.get("run_on_boot")):
        return False
    return True


def _remove_schedule_file(*, cron_id: str, plist_path: str) -> dict:
    removed = {
        "cron_id": cron_id,
        "plist_path": plist_path,
        "deleted": False,
    }
    plist = Path(plist_path) if plist_path else None
    if plist and platform.system() == "Darwin" and plist.exists():
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)],
            capture_output=True,
        )
        with contextlib.suppress(FileNotFoundError):
            plist.unlink()
            removed["deleted"] = True
    return removed


def ensure_personal_schedules(*, dry_run: bool = False) -> dict:
    """Create or repair personal schedules declared in inline script metadata."""
    classification = classify_scripts_dir()
    scripts = [entry for entry in classification["entries"] if entry["classification"] == "personal"]
    schedule_audit = audit_personal_schedules()
    schedules_by_path: dict[str, list[dict]] = {}
    for schedule in schedule_audit["schedules"]:
        schedules_by_path.setdefault(schedule["script_path"], []).append(schedule)

    report = {
        "ok": True,
        "dry_run": dry_run,
        "created": [],
        "repaired": [],
        "already_present": [],
        "skipped": [],
        "invalid": [],
    }

    for script in scripts:
        declared = script.get("declared_schedule", {})
        if not declared.get("required"):
            report["skipped"].append({
                "name": script["name"],
                "reason": "no declared schedule",
            })
            continue
        if not declared.get("valid"):
            report["invalid"].append({
                "name": script["name"],
                "path": script["path"],
                "error": declared.get("error", "invalid schedule metadata"),
            })
            continue

        existing = schedules_by_path.get(script["path"], [])
        matching = next((item for item in existing if item.get("schedule_managed") and _schedule_matches(item, declared)), None)
        if matching:
            report["already_present"].append({
                "name": script["name"],
                "cron_id": matching["cron_id"],
                "schedule_label": matching.get("schedule_label", ""),
            })
            continue

        repair_reasons = [item.get("schedule_state", item.get("schedule_origin", "unknown")) for item in existing]
        if dry_run:
            report["repaired" if existing else "created"].append({
                "name": script["name"],
                "cron_id": declared["cron_id"],
                "schedule_label": declared["schedule_label"],
                "dry_run": True,
                "reason": ", ".join(repair_reasons) if repair_reasons else "missing schedule",
            })
            continue

        removed = []
        if existing:
            for item in existing:
                removed.append(_remove_schedule_file(cron_id=item["cron_id"], plist_path=item.get("plist_path", "")))
            from db import delete_personal_script_schedule

            for item in existing:
                delete_personal_script_schedule(item["cron_id"])

        from plugins.schedule import handle_schedule_add

        response = handle_schedule_add(
            cron_id=declared["cron_id"],
            script=script["path"],
            schedule=declared.get("schedule", ""),
            interval_seconds=declared.get("interval_seconds", 0),
            description=script.get("description", ""),
            script_type=script.get("runtime", "auto"),
        )
        target = report["repaired" if existing else "created"]
        target.append({
            "name": script["name"],
            "cron_id": declared["cron_id"],
            "schedule_label": declared["schedule_label"],
            "reason": ", ".join(repair_reasons) if repair_reasons else "missing schedule",
            "removed": removed,
            "result": response,
        })

    sync_result = sync_personal_scripts()
    report["sync"] = sync_result
    report["classification"] = classification["summary"]
    return report


def reconcile_personal_scripts(*, dry_run: bool = False) -> dict:
    """Full lifecycle reconciliation: classify, sync registry, ensure declared schedules."""
    sync_result = sync_personal_scripts()
    ensure_result = ensure_personal_schedules(dry_run=dry_run)
    return {
        "ok": True,
        "dry_run": dry_run,
        "sync": sync_result,
        "ensure_schedules": ensure_result,
        "classification": ensure_result.get("classification", sync_result.get("classification", {})),
    }


def _template_path(filename: str) -> Path | None:
    candidates = [
        NEXO_HOME / "templates" / filename,
        NEXO_CODE.parent / "templates" / filename,
        NEXO_CODE / "templates" / filename,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _script_filename_from_name(name: str, runtime: str) -> str:
    slug = []
    for ch in name.strip().lower():
        if ch.isalnum():
            slug.append(ch)
        elif ch in {" ", "-", "_"}:
            slug.append("-")
    stem = "".join(slug).strip("-") or "personal-script"
    ext = {
        "python": ".py",
        "shell": ".sh",
        "node": ".js",
        "php": ".php",
    }.get(runtime, ".py")
    return stem + ext


def create_script(name: str, *, description: str = "", runtime: str = "python", force: bool = False) -> dict:
    runtime = runtime if runtime in SUPPORTED_RUNTIMES else "python"
    if runtime == "unknown":
        runtime = "python"

    scripts_dir = get_scripts_dir()
    scripts_dir.mkdir(parents=True, exist_ok=True)
    filename = _script_filename_from_name(name, runtime)
    path = scripts_dir / filename
    if path.exists() and not force:
        raise FileExistsError(f"Script already exists: {path}")

    if runtime == "shell":
        template_path = _template_path("script-template.sh")
    else:
        template_path = _template_path("script-template.py")

    if template_path:
        content = template_path.read_text()
    elif runtime == "shell":
        content = (
            "#!/usr/bin/env bash\n"
            "# nexo: name=example-script\n"
            "# nexo: description=Example shell script using NEXO\n"
            "# nexo: runtime=shell\n"
            "set -euo pipefail\n"
            "echo \"Hello from NEXO personal script\"\n"
        )
    else:
        content = (
            "#!/usr/bin/env python3\n"
            "# nexo: name=example-script\n"
            "# nexo: description=Example personal script using NEXO\n"
            "# nexo: runtime=python\n"
            "print('hello')\n"
        )

    script_name = Path(filename).stem
    content = content.replace("example-script", script_name)
    content = content.replace("Example personal script using the stable NEXO CLI", description or f"Personal script: {script_name}")
    content = content.replace("Example shell script using NEXO", description or f"Personal script: {script_name}")

    path.write_text(content)
    if runtime in {"shell", "python"}:
        path.chmod(0o755)
    sync_result = sync_personal_scripts()
    return {
        "ok": True,
        "name": script_name,
        "path": str(path),
        "runtime": runtime,
        "description": description,
        "sync": sync_result,
    }


def unschedule_personal_script(name_or_path: str) -> dict:
    """Remove all personal schedules attached to a script and prune registry entries."""
    from db import (
        init_db,
        get_personal_script,
        delete_personal_script_schedule,
    )

    init_db()
    sync_personal_scripts()
    script = get_personal_script(name_or_path)
    if not script:
        resolved = resolve_script(name_or_path)
        if not resolved or resolved.get("core"):
            return {"ok": False, "error": f"Personal script not found: {name_or_path}"}
        script = resolved

    removed: list[dict] = []
    audited = audit_personal_schedules()
    discovered = [
        item for item in audited["schedules"]
        if item.get("script_path") == script.get("path")
    ]
    for schedule in discovered:
        removed.append(_remove_schedule_file(cron_id=schedule["cron_id"], plist_path=schedule.get("plist_path", "")))

    for schedule in script.get("schedules", []):
        delete_personal_script_schedule(schedule["cron_id"])
        if not any(item["cron_id"] == schedule["cron_id"] for item in removed):
            removed.append({
                "cron_id": schedule["cron_id"],
                "plist_path": schedule.get("plist_path", ""),
                "deleted": False,
            })

    sync_result = sync_personal_scripts()
    return {
        "ok": True,
        "script": script["name"],
        "removed_schedules": removed,
        "sync": sync_result,
    }


def remove_personal_script(name_or_path: str, *, keep_file: bool = False) -> dict:
    """Remove a personal script from the runtime and registry."""
    from db import init_db, get_personal_script, delete_personal_script

    init_db()
    sync_personal_scripts()
    script = get_personal_script(name_or_path)
    if not script:
        resolved = resolve_script(name_or_path)
        if not resolved or resolved.get("core"):
            return {"ok": False, "error": f"Personal script not found: {name_or_path}"}
        script = resolved

    if script.get("core"):
        return {"ok": False, "error": "Refusing to remove a core script via personal scripts lifecycle."}

    unschedule_result = unschedule_personal_script(script["path"])
    deleted_file = False
    path = Path(script["path"])
    if not keep_file and path.is_file() and _within_scripts_dir(path):
        path.unlink()
        deleted_file = True
    delete_personal_script(script["path"])
    sync_result = sync_personal_scripts()
    return {
        "ok": True,
        "script": script["name"],
        "path": script["path"],
        "deleted_file": deleted_file,
        "keep_file": keep_file,
        "unschedule": unschedule_result,
        "sync": sync_result,
    }


def doctor_script(path_or_name: str) -> dict:
    """Validate a single script. Returns dict with pass/warn/fail items."""
    # Resolve
    p = Path(path_or_name)
    if not p.is_file():
        info = resolve_script(path_or_name)
        if not info:
            return {"status": "fail", "items": [{"level": "fail", "msg": f"Script not found: {path_or_name}"}]}
        p = Path(info["path"])

    items: list[dict] = []
    meta = parse_inline_metadata(p)
    runtime = classify_runtime(p, meta)
    core_names = load_core_script_names()
    is_core = p.name in core_names

    # File exists
    if p.is_file():
        items.append({"level": "pass", "msg": f"File exists: {p.name}"})
    else:
        items.append({"level": "fail", "msg": f"File missing: {p.name}"})
        return {"status": "fail", "items": items}

    # Name collision with core
    name = meta.get("name", p.stem)
    if not is_core:
        for core in core_names:
            core_stem = Path(core).stem
            if name == core_stem:
                items.append({"level": "fail", "msg": f"Name collision with core script: {core}"})

    # Runtime recognized
    if runtime == "unknown":
        items.append({"level": "warn", "msg": "Runtime not recognized (no shebang, no extension match)"})
    else:
        items.append({"level": "pass", "msg": f"Runtime: {runtime}"})

    # Shebang for shell scripts
    if runtime == "shell":
        shebang = _detect_shebang(p)
        if not shebang:
            items.append({"level": "warn", "msg": "Shell script without shebang"})
        else:
            items.append({"level": "pass", "msg": f"Shebang: {shebang}"})

    # Executable bit for shell scripts
    if runtime == "shell":
        mode = p.stat().st_mode
        if not (mode & stat.S_IXUSR):
            items.append({"level": "warn", "msg": "Shell script missing executable bit"})
        else:
            items.append({"level": "pass", "msg": "Executable bit set"})

    # Timeout parse
    timeout_str = meta.get("timeout", "")
    if timeout_str:
        try:
            int(timeout_str)
            items.append({"level": "pass", "msg": f"Timeout: {timeout_str}s"})
        except ValueError:
            items.append({"level": "fail", "msg": f"Invalid timeout value: {timeout_str}"})

    declared = get_declared_schedule(meta, name)
    if declared.get("required"):
        if declared.get("valid"):
            items.append({"level": "pass", "msg": f"Declared schedule: {declared['schedule_label']}"})
        else:
            items.append({"level": "fail", "msg": declared.get("error", "Invalid declared schedule metadata")})

    if runtime == "node" and not shutil.which("node"):
        items.append({"level": "fail", "msg": "Node runtime not found in PATH"})
    if runtime == "php" and not shutil.which("php"):
        items.append({"level": "fail", "msg": "PHP runtime not found in PATH"})

    # Requires check
    requires = meta.get("requires", "")
    if requires:
        for cmd in requires.split(","):
            cmd = cmd.strip()
            if cmd and not shutil.which(cmd):
                items.append({"level": "fail", "msg": f"Required command not in PATH: {cmd}"})
            elif cmd:
                items.append({"level": "pass", "msg": f"Required command found: {cmd}"})

    # Forbidden patterns (only for personal scripts)
    if not is_core:
        try:
            content = p.read_text(errors="ignore")
            for pat in _FORBIDDEN_PATTERNS:
                match = pat.search(content)
                if match:
                    items.append({"level": "fail", "msg": f"Forbidden DB pattern found: {match.group()}"})
        except Exception:
            pass

    # Determine overall status
    levels = [i["level"] for i in items]
    if "fail" in levels:
        status = "fail"
    elif "warn" in levels:
        status = "warn"
    else:
        status = "pass"

    return {"status": status, "items": items, "name": name, "path": str(p)}


def doctor_all_scripts() -> list[dict]:
    """Run doctor on all personal scripts."""
    results = []
    for script in list_scripts(include_core=False):
        result = doctor_script(script["path"])
        results.append(result)
    return results
