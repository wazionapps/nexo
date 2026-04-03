"""NEXO Script Registry — discovery, metadata, validation for personal scripts.

Scripts live in NEXO_HOME/scripts/. Core scripts (from manifest) are filtered by default.
Personal scripts use CLI as stable interface, never direct DB access.
"""
from __future__ import annotations

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

METADATA_KEYS = {"name", "description", "runtime", "timeout", "requires", "tools", "hidden"}
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


def list_scripts(include_core: bool = False) -> list[dict]:
    """List scripts in NEXO_HOME/scripts/.

    By default only personal scripts. With include_core=True, also shows core/cron scripts.
    """
    scripts_dir = get_scripts_dir()
    if not scripts_dir.is_dir():
        return []

    core_names = load_core_script_names()
    results = []

    for f in sorted(scripts_dir.iterdir()):
        if not f.is_file():
            continue
        if _is_ignored(f):
            continue

        meta = parse_inline_metadata(f)
        if not _is_script_candidate(f, meta):
            continue

        is_core = f.name in core_names
        if is_core and not include_core:
            continue

        runtime = classify_runtime(f, meta)
        name = meta.get("name", f.stem)
        hidden = meta.get("hidden", "").lower() in ("true", "1", "yes")

        if hidden and not include_core:
            continue

        results.append({
            "name": name,
            "runtime": runtime,
            "description": meta.get("description", ""),
            "path": str(f),
            "core": is_core,
            "metadata": meta,
        })

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
    scripts = list_scripts(include_core=False)
    schedules = discover_personal_schedules()
    return sync_personal_scripts_registry(scripts, schedules, prune_missing=prune_missing)


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
