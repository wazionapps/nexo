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
}
SUPPORTED_RUNTIMES = {"python", "shell", "node", "php", "unknown"}


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
    cron_id = metadata.get("cron_id", "").strip() or _safe_slug(default_name or metadata.get("name", "script"))
    interval_raw = metadata.get("interval_seconds", "").strip()
    schedule_raw = metadata.get("schedule", "").strip()
    required = _truthy(metadata.get("schedule_required")) or bool(interval_raw or schedule_raw)

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
        }

    return {
        "required": required,
        "valid": not required,
        "error": "" if not required else "schedule_required=true but no schedule metadata was provided.",
        "cron_id": cron_id,
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
    candidates: list[Path] = []
    for arg in program_args or []:
        if not isinstance(arg, str):
            continue
        candidate = Path(arg).expanduser()
        if not candidate.is_file():
            continue
        if not _within_scripts_dir(candidate):
            continue
        if _is_ignored(candidate):
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


def discover_personal_schedules() -> list[dict]:
    """Inspect system schedulers and return personal schedules linked to scripts."""
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
        script_path = _extract_script_path_from_program_args(program_args)
        if script_path is None:
            continue
        if script_path.name in core_names:
            continue

        schedule_type, schedule_value, schedule_label = _format_schedule_from_plist(plist_data)
        label = str(plist_data.get("Label", plist_path.stem))
        cron_id = label.replace("com.nexo.", "", 1)
        results.append({
            "cron_id": cron_id,
            "script_path": str(script_path),
            "schedule_type": schedule_type,
            "schedule_value": schedule_value,
            "schedule_label": schedule_label,
            "launchd_label": label,
            "plist_path": str(plist_path),
            "enabled": True,
            "description": "",
        })

    return results


def sync_personal_scripts(prune_missing: bool = True) -> dict:
    """Sync filesystem + scheduler state into the DB-backed personal scripts registry."""
    from db import init_db, sync_personal_scripts_registry

    init_db()
    classification = classify_scripts_dir()
    scripts = [entry for entry in classification["entries"] if entry["classification"] == "personal"]
    schedules = discover_personal_schedules()
    result = sync_personal_scripts_registry(scripts, schedules, prune_missing=prune_missing)
    result["classification"] = classification["summary"]
    missing_declared = []
    schedules_by_path: dict[str, list[dict]] = {}
    for schedule in schedules:
        schedules_by_path.setdefault(schedule["script_path"], []).append(schedule)
    for script in scripts:
        declared = script.get("declared_schedule", {})
        if not declared.get("required"):
            continue
        attached = schedules_by_path.get(script["path"], [])
        if not attached:
            missing_declared.append({
                "name": script["name"],
                "path": script["path"],
                "declared_schedule": declared,
            })
    result["missing_declared_schedules"] = missing_declared
    return result


def _schedule_matches(existing: dict, declared: dict) -> bool:
    if not existing or not declared.get("valid"):
        return False
    if existing.get("cron_id") != declared.get("cron_id"):
        return False
    if existing.get("schedule_type") != declared.get("schedule_type"):
        return False
    if str(existing.get("schedule_value", "")) != str(declared.get("schedule_value", "")):
        return False
    return True


def ensure_personal_schedules(*, dry_run: bool = False) -> dict:
    """Create or repair personal schedules declared in inline script metadata."""
    classification = classify_scripts_dir()
    scripts = [entry for entry in classification["entries"] if entry["classification"] == "personal"]
    discovered = discover_personal_schedules()
    schedules_by_path: dict[str, list[dict]] = {}
    for schedule in discovered:
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
        matching = next((item for item in existing if _schedule_matches(item, declared)), None)
        if matching:
            report["already_present"].append({
                "name": script["name"],
                "cron_id": matching["cron_id"],
                "schedule_label": matching.get("schedule_label", ""),
            })
            continue

        if dry_run:
            report["repaired" if existing else "created"].append({
                "name": script["name"],
                "cron_id": declared["cron_id"],
                "schedule_label": declared["schedule_label"],
                "dry_run": True,
            })
            continue

        if existing:
            unschedule_personal_script(script["name"])

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
        return {"ok": False, "error": f"Personal script not found: {name_or_path}"}

    removed: list[dict] = []
    for schedule in script.get("schedules", []):
        plist_path = schedule.get("plist_path", "")
        if plist_path:
            plist = Path(plist_path)
            if platform.system() == "Darwin" and plist.is_file():
                subprocess.run(
                    ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)],
                    capture_output=True,
                )
                with contextlib.suppress(FileNotFoundError):
                    plist.unlink()
        delete_personal_script_schedule(schedule["cron_id"])
        removed.append({
            "cron_id": schedule["cron_id"],
            "plist_path": plist_path,
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
