#!/usr/bin/env python3
"""
NEXO Cron Sync — Synchronize crons/manifest.json with system LaunchAgents (macOS).

Called by nexo_update after pulling new code. Ensures:
- New crons in manifest → installed
- Removed crons from manifest → unloaded + deleted
- Changed schedule/interval → plist updated + reloaded
- Personal (non-core) crons → left untouched

Usage:
  python3 crons/sync.py [--dry-run]

Environment:
  NEXO_HOME — root of NEXO installation
  NEXO_CODE — path to NEXO source (defaults to script parent's parent)
"""

import json
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_CRONS_DIR = Path(__file__).resolve().parent
_DEFAULT_RUNTIME_ROOT = _CRONS_DIR.parent
_runtime_root = Path(os.environ.get("NEXO_CODE", str(_DEFAULT_RUNTIME_ROOT)))
if str(_runtime_root) not in sys.path:
    sys.path.insert(0, str(_runtime_root))

import paths
from cron_recovery import is_cron_enabled, resolve_declared_schedule, should_run_at_load
try:
    from windows_runtime import resolve_windows_host_binary, running_inside_wsl
except ImportError:
    def resolve_windows_host_binary(command: str) -> str:
        return ""

    def running_inside_wsl() -> bool:
        return False
try:
    from runtime_power import (
        launchctl_side_effects_allowed,
        reload_launchagent_plist,
        resolve_launchagent_path,
        unload_launchagent_plist,
    )
except ImportError:
    def resolve_launchagent_path() -> str:
        """Fallback when runtime_power is not importable."""
        home = Path.home()
        parts = [
            str(home / ".nexo/runtime/bootstrap/npm-global/bin"),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            str(home / ".local/bin"),
            str(home / ".nexo/bin"),
        ]
        nvm_dir = home / ".nvm/versions/node"
        if nvm_dir.is_dir():
            versions = sorted(nvm_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            for v in versions:
                node_bin = v / "bin"
                if (node_bin / "node").exists():
                    parts.insert(0, str(node_bin))
                    break
        return ":".join(parts)

    def launchctl_side_effects_allowed() -> bool:
        """Fallback guard when runtime_power is unavailable."""
        if str(os.environ.get("NEXO_ALLOW_EPHEMERAL_INSTALL", "")).strip() == "1":
            return True

        def normalize(candidate: str | os.PathLike[str] | None) -> str:
            if not candidate:
                return ""
            try:
                resolved = Path(candidate).expanduser().resolve(strict=False)
            except Exception:
                try:
                    resolved = Path(candidate).expanduser()
                except Exception:
                    return ""
            return str(resolved).replace("\\", "/").rstrip("/")

        temp_roots: set[str] = set()
        for root in (tempfile.gettempdir(), "/tmp", "/private/tmp", "/var/folders", "/private/var/folders"):
            normalized = normalize(root)
            if not normalized:
                continue
            temp_roots.add(normalized)
            if normalized == "/tmp":
                temp_roots.add("/private/tmp")
            elif normalized == "/private/tmp":
                temp_roots.add("/tmp")
            elif normalized.startswith("/var/"):
                temp_roots.add(f"/private{normalized}")
            elif normalized.startswith("/private/var/"):
                temp_roots.add(normalized.removeprefix("/private"))

        candidates = (
            normalize(os.environ.get("HOME", str(Path.home()))),
            normalize(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))),
        )
        return not any(
            candidate and root and (candidate == root or candidate.startswith(f"{root}/"))
            for candidate in candidates
            for root in temp_roots
        )

    def reload_launchagent_plist(plist_path: Path, label: str | None = None, timeout: int = 10) -> dict:
        if not launchctl_side_effects_allowed():
            return {"ok": True, "label": label or Path(plist_path).stem, "action": "skipped-ephemeral-runtime"}
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
        proc = subprocess.run(["launchctl", "load", "-w", str(plist_path)], capture_output=True, text=True, timeout=timeout)
        if proc.returncode == 0:
            return {"ok": True, "label": label or Path(plist_path).stem}
        return {"ok": False, "label": label or Path(plist_path).stem, "error": proc.stderr or proc.stdout or "load failed"}

    def unload_launchagent_plist(plist_path: Path, label: str | None = None, timeout: int = 10) -> dict:
        if not launchctl_side_effects_allowed():
            return {"ok": True, "label": label or Path(plist_path).stem, "action": "skipped-ephemeral-runtime"}
        proc = subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, text=True, timeout=timeout)
        return {"ok": proc.returncode == 0, "label": label or Path(plist_path).stem}

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
SOURCE_ROOT = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent.parent)))
RUNTIME_ROOT = NEXO_HOME
MANIFEST = Path(__file__).resolve().parent / "manifest.json"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LABEL_PREFIX = "com.nexo."
LOG_DIR = paths.logs_dir()
OPTIONALS_FILE = paths.config_dir() / "optionals.json"
SCHEDULE_FILE = paths.config_dir() / "schedule.json"
CORE_CRON_MANAGED_ENV = "NEXO_MANAGED_CORE_CRON"
PERSONAL_CRON_MANAGED_ENV = "NEXO_MANAGED_PERSONAL_CRON"
PERSONAL_CRON_ID_ENV = "NEXO_PERSONAL_CRON_ID"
CRONTAB_BEGIN = "# >>> NEXO managed core crons >>>"
CRONTAB_END = "# <<< NEXO managed core crons <<<"
RETIRED_CORE_FILES = (
    Path("core") / "scripts" / "nexo-day-orchestrator.sh",
    Path("scripts") / "nexo-day-orchestrator.sh",
)


def _resolve_core_python_bin() -> str:
    """Prefer the NEXO-managed Python for core cron execution."""
    candidates = [
        os.environ.get("NEXO_RUNTIME_PYTHON", ""),
        os.environ.get("NEXO_PYTHON", ""),
        str(RUNTIME_ROOT / ".venv" / "bin" / "python3"),
        str(RUNTIME_ROOT / ".venv" / "bin" / "python"),
        str(_runtime_code_dir() / ".venv" / "bin" / "python3"),
        str(_runtime_code_dir() / ".venv" / "bin" / "python"),
    ]
    if platform.system() == "Darwin":
        candidates.extend(
            [
                "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
                "/opt/homebrew/bin/python3.12",
                "/usr/local/bin/python3.12",
                "/opt/homebrew/bin/python3",
                "/usr/local/bin/python3",
                "/usr/bin/python3",
            ]
        )
    else:
        candidates.extend(["/usr/bin/python3", "/usr/local/bin/python3", "python3"])

    for candidate in candidates:
        if not candidate:
            continue
        expanded = Path(str(candidate)).expanduser()
        if expanded.exists():
            return str(expanded)
        if os.sep not in str(candidate) and shutil.which(str(candidate)):
            return str(candidate)
    return "python3"


def _runtime_scripts_dir() -> Path:
    new = RUNTIME_ROOT / "core" / "scripts"
    legacy = RUNTIME_ROOT / "scripts"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def _runtime_code_dir() -> Path:
    packaged = RUNTIME_ROOT / "core"
    if packaged.exists() or not (RUNTIME_ROOT / "server.py").exists():
        return packaged
    return RUNTIME_ROOT


def _runtime_crons_dir() -> Path:
    new = RUNTIME_ROOT / "runtime" / "crons"
    legacy = RUNTIME_ROOT / "crons"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def log(msg: str):
    print(f"[cron-sync] {msg}", flush=True)


def _sync_watchdog_hash_registry():
    """Keep the immutable-hash registry aligned with the runtime watchdog script."""
    try:
        scripts_dir = _runtime_scripts_dir()
        watchdog_path = scripts_dir / "nexo-watchdog.sh"
        if not watchdog_path.exists():
            return
        registry_path = scripts_dir / ".watchdog-hashes"
        entries: dict[str, str] = {}
        if registry_path.exists():
            for line in registry_path.read_text().splitlines():
                if "|" not in line:
                    continue
                file_path, expected_hash = line.split("|", 1)
                if file_path:
                    candidate = Path(file_path)
                    try:
                        if candidate.resolve(strict=False) == watchdog_path.resolve(strict=False):
                            continue
                    except Exception:
                        pass
                    entries[file_path] = expected_hash
        import hashlib
        entries[str(watchdog_path)] = hashlib.sha256(watchdog_path.read_bytes()).hexdigest()
        registry_path.write_text(
            "\n".join(f"{file_path}|{digest}" for file_path, digest in sorted(entries.items())) + "\n"
        )
    except Exception as e:
        log(f"WARNING: could not sync watchdog hash registry: {e}")


def _refresh_runtime_manifest():
    """Keep the installed crons manifest aligned with the source manifest."""
    try:
        runtime_manifest = _runtime_crons_dir() / "manifest.json"
        runtime_manifest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(MANIFEST, runtime_manifest)
    except Exception as e:
        log(f"WARNING: could not refresh runtime manifest: {e}")


def _cleanup_retired_core_files():
    """Remove retired core runtime files that should no longer survive updates."""
    for rel_path in RETIRED_CORE_FILES:
        try:
            target = RUNTIME_ROOT / rel_path
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                log(f"  Removed retired core file: {rel_path}")
        except Exception as e:
            log(f"WARNING: could not remove retired core file {rel_path}: {e}")


def load_manifest() -> list[dict]:
    try:
        from automation_controls import apply_core_automation_overrides
    except Exception:
        apply_core_automation_overrides = None
    try:
        from core_schedule_controls import apply_core_schedule_overrides
    except Exception:
        apply_core_schedule_overrides = None
    try:
        from product_mode import filter_blocked_crons
    except Exception:
        filter_blocked_crons = None

    with open(MANIFEST) as f:
        data = json.load(f)
    crons = data.get("crons", [])

    enabled_optionals: dict[str, bool] = {}
    if OPTIONALS_FILE.is_file():
        try:
            enabled_optionals = json.loads(OPTIONALS_FILE.read_text())
        except Exception as e:
            log(f"WARNING: could not read optionals.json: {e}")

    schedule_data: dict = {}
    if SCHEDULE_FILE.is_file():
        try:
            loaded = json.loads(SCHEDULE_FILE.read_text())
            if isinstance(loaded, dict):
                schedule_data = loaded
        except Exception:
            pass

    filtered = []
    for cron in crons:
        if not is_cron_enabled(
            cron,
            optionals=enabled_optionals,
            schedule_data=schedule_data,
            system=platform.system(),
        ):
            continue
        filtered.append(cron)
    if callable(filter_blocked_crons):
        try:
            filtered = filter_blocked_crons(filtered)
        except Exception as e:
            log(f"WARNING: could not filter product-blocked crons: {e}")
    if callable(apply_core_automation_overrides):
        try:
            filtered = apply_core_automation_overrides(filtered)
        except Exception as e:
            log(f"WARNING: could not apply core automation overrides: {e}")
    if callable(apply_core_schedule_overrides):
        try:
            filtered = apply_core_schedule_overrides(filtered)
        except Exception as e:
            log(f"WARNING: could not apply core schedule overrides: {e}")
    return filtered


def _source_runtime_mappings() -> list[tuple[list[Path], Path]]:
    """Map source/runtime roots to the canonical F0.6 runtime targets."""
    return [
        (
            [
                SOURCE_ROOT / "scripts",
                RUNTIME_ROOT / "scripts",
                paths.core_scripts_dir(),
            ],
            Path("core") / "scripts",
        ),
        (
            [
                SOURCE_ROOT / "crons",
                RUNTIME_ROOT / "crons",
                paths.crons_dir(),
            ],
            Path("runtime") / "crons",
        ),
        (
            [
                SOURCE_ROOT / "hooks",
                RUNTIME_ROOT / "hooks",
                paths.core_hooks_dir(),
            ],
            Path("core") / "hooks",
        ),
    ]


def _resolve_source_artifact(relative_path: str | Path) -> Path:
    """Resolve a manifest/runtime relative path across repo and F0.6 runtime layouts."""
    rel = Path(relative_path)
    direct = SOURCE_ROOT / rel
    if direct.exists():
        return direct

    for roots, _target_root in _source_runtime_mappings():
        for root in roots:
            candidate = root / Path(*rel.parts[1:]) if rel.parts else root
            if rel.parts and root.name == rel.parts[0] and candidate.exists():
                return candidate
    return direct


def _runtime_relative_path(src: Path) -> Path:
    """Return the canonical path inside NEXO_HOME for a core artifact."""
    resolved = src.resolve(strict=False)
    for roots, target_root in _source_runtime_mappings():
        for root in roots:
            try:
                relative = resolved.relative_to(root.resolve(strict=False))
            except Exception:
                continue
            return target_root / relative

    # Best effort fallback for unexpected inputs.
    return Path("core") / "scripts" / resolved.name


def _copy_into_runtime(src: Path) -> Path:
    """Copy a script or directory from the source tree into NEXO_HOME.

    LaunchAgents should execute from NEXO_HOME, not directly from repo paths
    under macOS-protected folders such as ~/Documents.
    """
    dest = RUNTIME_ROOT / _runtime_relative_path(src)
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        if dest.exists() and src.resolve() == dest.resolve():
            if src.is_file() and (src.suffix in {".sh", ".py"} or os.access(src, os.X_OK)):
                dest.chmod(0o755)
            return dest
    except Exception:
        pass

    if src.is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        return dest

    shutil.copy2(src, dest)
    if src.suffix in {".sh", ".py"} or os.access(src, os.X_OK):
        dest.chmod(0o755)
    return dest


def _calendar_weekdays(schedule: dict) -> list[int]:
    raw = schedule.get("weekdays") or schedule.get("Weekdays")
    if raw is None and "weekday" in schedule:
        raw = [schedule.get("weekday")]
    if raw is None and "Weekday" in schedule:
        raw = [schedule.get("Weekday")]
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.replace("+", ",").split(",")]
    elif isinstance(raw, (list, tuple, set)):
        parts = list(raw)
    else:
        return []
    selected: set[int] = set()
    for part in parts:
        try:
            selected.add(int(part) % 7)
        except Exception:
            continue
    if len(selected) >= 7:
        return []
    return [day for day in (1, 2, 3, 4, 5, 6, 0) if day in selected]


def _launchd_calendar_intervals(schedule: dict) -> dict | list[dict]:
    base = {}
    if "hour" in schedule:
        base["Hour"] = schedule["hour"]
    if "minute" in schedule:
        base["Minute"] = schedule["minute"]
    weekdays = _calendar_weekdays(schedule)
    if weekdays:
        if len(weekdays) == 1:
            return {**base, "Weekday": weekdays[0]}
        return [{**base, "Weekday": day} for day in weekdays]
    return base


def build_plist(cron: dict) -> dict:
    """Build a macOS LaunchAgent plist dict from a manifest entry."""
    cron_id = cron["id"]
    label = f"{LABEL_PREFIX}{cron_id}"
    script_src = _resolve_source_artifact(cron["script"])
    script_type = cron.get("type", "python")

    # Copy scripts into NEXO_HOME preserving the source tree layout.
    script_dest = _copy_into_runtime(script_src)
    script_path = str(script_dest)

    # Also copy the wrapper and any subdirectories (e.g., deep-sleep/)
    wrapper_src = _resolve_source_artifact("scripts/nexo-cron-wrapper.sh")
    wrapper_dest = _copy_into_runtime(wrapper_src)
    wrapper_path = str(wrapper_dest)

    # Copy script subdirectories if they exist (e.g., deep-sleep/ for nexo-deep-sleep.sh)
    script_name = script_src.stem  # e.g., "nexo-deep-sleep"
    subdir_name = script_name.replace("nexo-", "")  # e.g., "deep-sleep"
    subdir_src = _resolve_source_artifact(Path("scripts") / subdir_name)
    if subdir_src.is_dir():
        _copy_into_runtime(subdir_src)

    python_bin = _resolve_core_python_bin()
    if script_type == "shell":
        program_args = ["/bin/bash", wrapper_path, cron_id, "/bin/bash", script_path]
    else:
        program_args = ["/bin/bash", wrapper_path, cron_id, python_bin, script_path]

    plist = {
        "Label": label,
        "ProgramArguments": program_args,
        "StandardOutPath": str(LOG_DIR / f"{cron_id}-stdout.log"),
        "StandardErrorPath": str(LOG_DIR / f"{cron_id}-stderr.log"),
        "EnvironmentVariables": {
            "PATH": resolve_launchagent_path(),
            "HOME": str(Path.home()),
            "NEXO_HOME": str(NEXO_HOME),
            "NEXO_CODE": str(_runtime_code_dir()),
            "NEXO_SOURCE_CODE": str(SOURCE_ROOT),
            "NEXO_MANAGED_CORE_CRON": "1",
            "NEXO_RUNTIME_PYTHON": python_bin,
            "PYTHONUNBUFFERED": "1",
        },
    }

    # Schedule
    if cron.get("keep_alive"):
        plist["RunAtLoad"] = True
        plist["KeepAlive"] = True
    else:
        if should_run_at_load(cron):
            plist["RunAtLoad"] = True
    if cron.get("watch_paths"):
        plist["WatchPaths"] = [
            str(Path(str(path)).expanduser()) if str(path).startswith("~") else str(path)
            for path in cron.get("watch_paths", [])
        ]
    if "interval_seconds" in cron and not cron.get("keep_alive"):
        plist["StartInterval"] = cron["interval_seconds"]
    elif "schedule" in cron and not cron.get("keep_alive"):
        s = resolve_declared_schedule(cron)
        plist["StartCalendarInterval"] = _launchd_calendar_intervals(s)

    return plist


def _shell_join(args: list[str | Path]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)


def _cron_schedule(cron: dict) -> str | None:
    if cron.get("keep_alive"):
        return None
    if "interval_seconds" in cron:
        try:
            seconds = int(cron["interval_seconds"])
        except Exception:
            return None
        if seconds <= 0 or seconds % 60 != 0:
            return None
        minutes = max(1, seconds // 60)
        return "* * * * *" if minutes == 1 else f"*/{minutes} * * * *"
    if "schedule" in cron:
        s = resolve_declared_schedule(cron)
        hour, minute = int(s.get("hour", 0)), int(s.get("minute", 0))
        weekday = "*"
        weekdays = _calendar_weekdays(s)
        if weekdays:
            weekday = ",".join("0" if int(day) == 7 else str(int(day) % 7) for day in weekdays)
        return f"{minute} {hour} * * {weekday}"
    return None


def _linux_crontab_entry(cron: dict, exec_cmd: str, stdout_log: Path, stderr_log: Path) -> str | None:
    schedule = _cron_schedule(cron)
    if not schedule:
        return None
    env_prefix = " ".join(
        f"{key}={shlex.quote(str(value))}"
        for key, value in {
            "HOME": Path.home(),
            "NEXO_HOME": NEXO_HOME,
            "NEXO_CODE": _runtime_code_dir(),
            "NEXO_RUNTIME_PYTHON": _resolve_core_python_bin(),
            "PYTHONUNBUFFERED": "1",
        }.items()
    )
    return f"{schedule} {env_prefix} {exec_cmd} >> {shlex.quote(str(stdout_log))} 2>> {shlex.quote(str(stderr_log))}"


def _strip_managed_crontab_block(body: str) -> str:
    lines = body.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == CRONTAB_BEGIN:
            skipping = True
            continue
        if line.strip() == CRONTAB_END:
            skipping = False
            continue
        if not skipping:
            kept.append(line)
    return "\n".join(kept).rstrip()


def _install_linux_crontab_fallback(entries: list[str]) -> dict:
    if not entries:
        return {"ok": False, "error": "no_crontab_entries"}
    if not shutil.which("crontab"):
        return {"ok": False, "error": "crontab_missing"}

    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    current_body = existing.stdout if existing.returncode == 0 else ""
    unmanaged_body = _strip_managed_crontab_block(current_body)
    managed_body = "\n".join([CRONTAB_BEGIN, *entries, CRONTAB_END])
    next_body = f"{unmanaged_body}\n\n{managed_body}\n" if unmanaged_body else f"{managed_body}\n"

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
            tmp_path = fh.name
            fh.write(next_body)
        proc = subprocess.run(["crontab", tmp_path], capture_output=True, text=True)
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr or proc.stdout or "crontab_install_failed"}
    return {"ok": True, "entries": len(entries)}


def _powershell_single_quote(value: str | os.PathLike[str]) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _windows_argument_quote(value: str | os.PathLike[str]) -> str:
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sync_wsl_windows_host_local_index_task(dry_run: bool = False) -> dict:
    if not running_inside_wsl():
        return {"ok": True, "skipped": True, "reason": "not_wsl"}
    powershell = resolve_windows_host_binary("powershell.exe")
    if not powershell:
        log("WARNING: Windows host PowerShell not available; local-index host task not installed.")
        return {"ok": False, "skipped": True, "reason": "powershell_missing"}

    distro = str(os.environ.get("WSL_DISTRO_NAME", "")).strip()
    if not distro:
        log("WARNING: WSL_DISTRO_NAME missing; local-index host task not installed.")
        return {"ok": False, "skipped": True, "reason": "wsl_distro_missing"}

    python_bin = _resolve_core_python_bin()
    script_path = _runtime_code_dir() / "scripts" / "nexo-local-index.py"
    command = (
        f"cd {shlex.quote(str(Path.home()))} && "
        f"NEXO_HOME={shlex.quote(str(NEXO_HOME))} "
        f"NEXO_CODE={shlex.quote(str(_runtime_code_dir()))} "
        f"NEXO_RUNTIME_PYTHON={shlex.quote(python_bin)} "
        f"{shlex.quote(python_bin)} {shlex.quote(str(script_path))}"
    )
    wsl_args = " ".join(
        _windows_argument_quote(arg)
        for arg in ("-d", distro, "--exec", "/bin/bash", "-lc", command)
    )
    task_name = "NEXO Local Memory"
    ps_script = (
        f"$action = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument {_powershell_single_quote(wsl_args)}; "
        "$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
        "-RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650); "
        "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-StartWhenAvailable -MultipleInstances IgnoreNew; "
        f"Register-ScheduledTask -TaskName {_powershell_single_quote(task_name)} -Action $action -Trigger $trigger "
        "-Settings $settings -Description 'NEXO Local Memory background indexing' -Force | Out-Null; "
        f"Start-ScheduledTask -TaskName {_powershell_single_quote(task_name)}"
    )

    if dry_run:
        log(f"  DRY-RUN: would install Windows host task: {task_name}")
        return {"ok": True, "dry_run": True, "task_name": task_name, "argument": wsl_args}

    result = subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script], capture_output=True, text=True, timeout=45)
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "windows_host_task_install_failed").strip()
        log(f"WARNING: Windows host local-index task install failed: {error}")
        return {"ok": False, "task_name": task_name, "error": error}
    log(f"Windows host task installed: {task_name}")
    return {"ok": True, "task_name": task_name, "argument": wsl_args}


def _enable_systemd_user_units(units: list[str]) -> dict:
    errors: list[str] = []
    daemon = subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
    if daemon.returncode != 0:
        errors.append(daemon.stderr or daemon.stdout or "systemctl daemon-reload failed")
    for unit in units:
        proc = subprocess.run(["systemctl", "--user", "enable", "--now", unit], capture_output=True, text=True)
        if proc.returncode != 0:
            errors.append(f"{unit}: {proc.stderr or proc.stdout or 'enable failed'}")
    return {"ok": not errors, "errors": errors}


def get_installed_nexo_crons() -> dict[str, Path]:
    """Return dict of cron_id → plist_path for installed NEXO crons."""
    installed = {}
    if not LAUNCH_AGENTS_DIR.exists():
        return installed
    for f in LAUNCH_AGENTS_DIR.glob(f"{LABEL_PREFIX}*.plist"):
        cron_id = f.stem.replace(LABEL_PREFIX, "")
        installed[cron_id] = f
    return installed


def plist_needs_update(existing_path: Path, new_plist: dict) -> bool:
    """Check if the installed plist differs from what we'd generate."""
    try:
        with open(existing_path, "rb") as f:
            existing = plistlib.load(f)
    except Exception:
        return True

    # Compare key fields
    if existing.get("ProgramArguments") != new_plist.get("ProgramArguments"):
        return True
    if existing.get("StartInterval") != new_plist.get("StartInterval"):
        return True
    if existing.get("StartCalendarInterval") != new_plist.get("StartCalendarInterval"):
        return True
    if existing.get("RunAtLoad") != new_plist.get("RunAtLoad"):
        return True
    if existing.get("KeepAlive") != new_plist.get("KeepAlive"):
        return True
    if existing.get("WatchPaths") != new_plist.get("WatchPaths"):
        return True
    if existing.get("EnvironmentVariables") != new_plist.get("EnvironmentVariables"):
        return True
    return False


def install_plist(label: str, plist: dict, plist_path: Path, dry_run: bool):
    """Write plist and load it."""
    if dry_run:
        log(f"  DRY-RUN: would install {plist_path.name}")
        return

    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)

    if not launchctl_side_effects_allowed():
        log(f"  Installed but skipped launchctl in ephemeral runtime: {plist_path.name}")
        return

    result = reload_launchagent_plist(plist_path, label=label)
    if result.get("action") == "skipped-ephemeral-runtime":
        log(f"  Installed but skipped launchctl in ephemeral runtime: {plist_path.name}")
        return
    if result.get("ok"):
        log(f"  Installed + loaded: {plist_path.name}")
    else:
        log(f"  Installed but launchctl reload failed: {plist_path.name}: {result.get('error') or 'unknown error'}")


def unload_plist(plist_path: Path, dry_run: bool):
    """Unload and remove a plist."""
    if dry_run:
        log(f"  DRY-RUN: would remove {plist_path.name}")
        return

    if not launchctl_side_effects_allowed():
        plist_path.unlink(missing_ok=True)
        log(f"  Removed without launchctl in ephemeral runtime: {plist_path.name}")
        return

    result = unload_launchagent_plist(plist_path)
    plist_path.unlink(missing_ok=True)
    if result.get("action") == "skipped-ephemeral-runtime":
        log(f"  Removed without launchctl in ephemeral runtime: {plist_path.name}")
    else:
        log(f"  Removed: {plist_path.name}")


def _plist_is_personal(existing: dict) -> bool:
    """Return True when a LaunchAgent is explicitly managed as a personal cron."""
    env = existing.get("EnvironmentVariables", {}) or {}
    return env.get(PERSONAL_CRON_MANAGED_ENV) == "1" or bool(env.get(PERSONAL_CRON_ID_ENV))


def _core_launchagent_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    for root in (
        SOURCE_ROOT / "scripts",
        RUNTIME_ROOT / "core" / "scripts",
        RUNTIME_ROOT / "scripts",
        paths.core_scripts_dir(),
    ):
        normalized = root.expanduser()
        if normalized not in roots:
            roots.append(normalized)
    return tuple(roots)


def _program_arguments_point_to_core(existing: dict) -> bool:
    args = existing.get("ProgramArguments", []) or []
    for arg in args:
        try:
            candidate = Path(str(arg)).expanduser()
        except Exception:
            continue
        for root in _core_launchagent_roots():
            try:
                candidate.relative_to(root)
                return True
            except ValueError:
                continue
    return False


def _plist_is_core(existing: dict) -> bool:
    """Return True when a LaunchAgent should be treated as a core cron."""
    env = existing.get("EnvironmentVariables", {}) or {}
    if _plist_is_personal(existing):
        return False

    if env.get(CORE_CRON_MANAGED_ENV) == "1":
        return True

    if _program_arguments_point_to_core(existing):
        return True

    args = existing.get("ProgramArguments", [])
    arg_blob = " ".join(str(a) for a in args)
    return (
        "nexo-cron-wrapper.sh" in arg_blob
        and (str(SOURCE_ROOT) in arg_blob or str(NEXO_HOME) in arg_blob)
    )


def sync(dry_run: bool = False):
    system = platform.system()
    if system == "Linux":
        sync_linux(dry_run)
        _sync_wsl_windows_host_local_index_task(dry_run)
        return
    if system != "Darwin":
        log(f"Unsupported platform: {system}. Skipping.")
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    manifest_crons = load_manifest()
    manifest_ids = {c["id"] for c in manifest_crons}
    installed = get_installed_nexo_crons()

    log(f"Manifest: {len(manifest_crons)} core crons")
    log(f"Installed: {len(installed)} NEXO crons")

    # 1. Install or update crons from manifest
    for cron in manifest_crons:
        cron_id = cron["id"]
        label = f"{LABEL_PREFIX}{cron_id}"
        plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
        new_plist = build_plist(cron)

        if cron_id not in installed:
            log(f"  NEW: {cron_id}")
            install_plist(label, new_plist, plist_path, dry_run)
        elif plist_needs_update(installed[cron_id], new_plist):
            log(f"  UPDATE: {cron_id}")
            install_plist(label, new_plist, plist_path, dry_run)
        else:
            log(f"  OK: {cron_id}")

    # 2. Remove crons that are in installed but NOT in manifest and ARE core
    #    (personal crons like shopify-backup are left alone; manifest-owned
    #     core automations such as email-monitor/followup-runner are tracked
    #     by the manifest and should not appear as "personal" examples here)
    for cron_id, plist_path in installed.items():
        if cron_id not in manifest_ids:
            try:
                with open(plist_path, "rb") as f:
                    existing = plistlib.load(f)
                is_core = _plist_is_core(existing)
            except Exception:
                is_core = False

            if is_core:
                log(f"  REMOVE (no longer in manifest): {cron_id}")
                unload_plist(plist_path, dry_run)
            else:
                log(f"  SKIP (personal): {cron_id}")

    _cleanup_retired_core_files()
    _refresh_runtime_manifest()
    _sync_watchdog_hash_registry()
    log("Sync complete.")


def sync_linux(dry_run: bool = False):
    """Sync manifest to systemd user timers (Linux)."""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    manifest_crons = load_manifest()
    wrapper_src = _resolve_source_artifact("scripts/nexo-cron-wrapper.sh")
    wrapper_dest = _copy_into_runtime(wrapper_src)

    log(f"Manifest: {len(manifest_crons)} core crons")

    python_bin = _resolve_core_python_bin()

    enable_units: list[str] = []
    crontab_entries: list[str] = []

    for cron in manifest_crons:
        cron_id = cron["id"]
        script_src = _resolve_source_artifact(cron["script"])
        script_dest = _copy_into_runtime(script_src)
        script_type = cron.get("type", "python")

        # Copy subdirectories
        subdir_name = script_src.stem.replace("nexo-", "")
        subdir_src = _resolve_source_artifact(Path("scripts") / subdir_name)
        if subdir_src.is_dir():
            _copy_into_runtime(subdir_src)

        if script_type == "shell":
            exec_cmd = _shell_join(["/bin/bash", wrapper_dest, cron_id, "/bin/bash", script_dest])
        else:
            exec_cmd = _shell_join(["/bin/bash", wrapper_dest, cron_id, python_bin, script_dest])

        service_path = unit_dir / f"nexo-{cron_id}.service"
        timer_path = unit_dir / f"nexo-{cron_id}.timer"

        stdout_log = LOG_DIR / f"{cron_id}-stdout.log"
        stderr_log = LOG_DIR / f"{cron_id}-stderr.log"

        service_type = "simple" if cron.get("keep_alive") else "oneshot"
        restart_block = "Restart=always\nRestartSec=5\n" if cron.get("keep_alive") else ""
        install_block = "\n[Install]\nWantedBy=default.target\n" if cron.get("keep_alive") else ""
        service_content = f"""[Unit]
Description=NEXO: {cron.get('description', cron_id)}

[Service]
Type={service_type}
ExecStart={exec_cmd}
Environment=NEXO_HOME={NEXO_HOME}
Environment=NEXO_CODE={_runtime_code_dir()}
Environment=NEXO_RUNTIME_PYTHON={python_bin}
Environment=HOME={Path.home()}
StandardOutput=append:{stdout_log}
StandardError=append:{stderr_log}
{restart_block}{install_block}"""

        if cron.get("keep_alive"):
            timer_spec = ""
        elif cron.get("run_at_load"):
            timer_spec = "OnBootSec=0"
        elif "interval_seconds" in cron:
            timer_spec = f"OnUnitActiveSec={cron['interval_seconds']}s\nOnBootSec=60s"
        elif "schedule" in cron:
            s = resolve_declared_schedule(cron)
            h, m = s.get("hour", 0), s.get("minute", 0)
            weekdays = _calendar_weekdays(s)
            if weekdays:
                days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                timer_spec = "\n".join(
                    f"OnCalendar={days[int(day) % 7]} *-*-* {h:02d}:{m:02d}:00"
                    for day in weekdays
                )
            else:
                timer_spec = f"OnCalendar=*-*-* {h:02d}:{m:02d}:00"
        else:
            log(f"  SKIP {cron_id}: no schedule or interval")
            continue

        if dry_run:
            log(f"  DRY-RUN: would install {cron_id}")
            continue

        service_path.write_text(service_content)
        if cron.get("keep_alive"):
            enable_units.append(f"nexo-{cron_id}.service")
            log(f"  Installed keep_alive service: {cron_id}")
            continue

        timer_content = f"""[Unit]
Description=NEXO timer: {cron.get('description', cron_id)}

[Timer]
{timer_spec}
Persistent=true

[Install]
WantedBy=timers.target
"""
        timer_path.write_text(timer_content)
        enable_units.append(f"nexo-{cron_id}.timer")
        crontab_entry = _linux_crontab_entry(cron, exec_cmd, stdout_log, stderr_log)
        if crontab_entry:
            crontab_entries.append(crontab_entry)
        log(f"  Installed: {cron_id}")

    if not dry_run:
        systemd_result = _enable_systemd_user_units(enable_units)
        if systemd_result.get("ok"):
            log("systemd units enabled.")
        else:
            log(f"WARNING: systemd user timers failed; installing crontab fallback: {systemd_result.get('errors')}")
            fallback = _install_linux_crontab_fallback(crontab_entries)
            if not fallback.get("ok"):
                raise RuntimeError(
                    "Linux cron activation failed: "
                    f"systemd={systemd_result.get('errors')} crontab={fallback.get('error')}"
                )
            log(f"crontab fallback installed ({fallback.get('entries')} entries).")

    log("Sync complete.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        log("DRY RUN MODE — no changes will be made")
    sync(dry_run=dry_run)
