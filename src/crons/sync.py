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
import shutil
import subprocess
import sys
from pathlib import Path

_CRONS_DIR = Path(__file__).resolve().parent
_DEFAULT_RUNTIME_ROOT = _CRONS_DIR.parent
_runtime_root = Path(os.environ.get("NEXO_CODE", str(_DEFAULT_RUNTIME_ROOT)))
if str(_runtime_root) not in sys.path:
    sys.path.insert(0, str(_runtime_root))

from cron_recovery import should_run_at_load

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
SOURCE_ROOT = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent.parent)))
RUNTIME_ROOT = NEXO_HOME
MANIFEST = Path(__file__).resolve().parent / "manifest.json"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LABEL_PREFIX = "com.nexo."
LOG_DIR = NEXO_HOME / "logs"
OPTIONALS_FILE = NEXO_HOME / "config" / "optionals.json"
SCHEDULE_FILE = NEXO_HOME / "config" / "schedule.json"
RETIRED_CORE_FILES = (
    Path("scripts") / "nexo-day-orchestrator.sh",
)


def log(msg: str):
    print(f"[cron-sync] {msg}", flush=True)


def _sync_watchdog_hash_registry():
    """Keep the immutable-hash registry aligned with the runtime watchdog script."""
    try:
        watchdog_path = RUNTIME_ROOT / "scripts" / "nexo-watchdog.sh"
        if not watchdog_path.exists():
            return
        registry_path = RUNTIME_ROOT / "scripts" / ".watchdog-hashes"
        entries: dict[str, str] = {}
        if registry_path.exists():
            for line in registry_path.read_text().splitlines():
                if "|" not in line:
                    continue
                file_path, expected_hash = line.split("|", 1)
                if file_path:
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
        runtime_manifest = RUNTIME_ROOT / "crons" / "manifest.json"
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
    with open(MANIFEST) as f:
        data = json.load(f)
    crons = data.get("crons", [])

    enabled_optionals: dict[str, bool] = {}
    if OPTIONALS_FILE.is_file():
        try:
            enabled_optionals = json.loads(OPTIONALS_FILE.read_text())
        except Exception as e:
            log(f"WARNING: could not read optionals.json: {e}")

    automation_default = True
    if SCHEDULE_FILE.is_file():
        try:
            schedule_data = json.loads(SCHEDULE_FILE.read_text())
            automation_default = bool(schedule_data.get("automation_enabled", True))
        except Exception:
            pass

    filtered = []
    for cron in crons:
        optional_key = cron.get("optional")
        if optional_key == "automation":
            enabled = enabled_optionals.get(optional_key, automation_default)
        else:
            enabled = enabled_optionals.get(optional_key, False)
        if optional_key and not enabled:
            continue
        filtered.append(cron)
    return filtered


def _runtime_relative_path(src: Path) -> Path:
    """Return the path inside NEXO_HOME that mirrors the source tree."""
    src = src.resolve()
    try:
        return src.relative_to(SOURCE_ROOT.resolve())
    except Exception:
        # Best effort fallback for unexpected inputs.
        return Path("scripts") / src.name


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


def build_plist(cron: dict) -> dict:
    """Build a macOS LaunchAgent plist dict from a manifest entry."""
    cron_id = cron["id"]
    label = f"{LABEL_PREFIX}{cron_id}"
    script_src = SOURCE_ROOT / cron["script"]
    script_type = cron.get("type", "python")

    # Copy scripts into NEXO_HOME preserving the source tree layout.
    script_dest = _copy_into_runtime(script_src)
    script_path = str(script_dest)

    # Also copy the wrapper and any subdirectories (e.g., deep-sleep/)
    wrapper_src = SOURCE_ROOT / "scripts" / "nexo-cron-wrapper.sh"
    wrapper_dest = _copy_into_runtime(wrapper_src)
    wrapper_path = str(wrapper_dest)

    # Copy script subdirectories if they exist (e.g., deep-sleep/ for nexo-deep-sleep.sh)
    script_name = script_src.stem  # e.g., "nexo-deep-sleep"
    subdir_name = script_name.replace("nexo-", "")  # e.g., "deep-sleep"
    subdir_src = SOURCE_ROOT / "scripts" / subdir_name
    if subdir_src.is_dir():
        _copy_into_runtime(subdir_src)

    if script_type == "shell":
        program_args = ["/bin/bash", wrapper_path, cron_id, "/bin/bash", script_path]
    else:
        # Find python3
        python_candidates = [
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
            "/usr/bin/python3",
        ]
        python_bin = "python3"
        for p in python_candidates:
            if Path(p).exists():
                python_bin = p
                break
        program_args = ["/bin/bash", wrapper_path, cron_id, python_bin, script_path]

    plist = {
        "Label": label,
        "ProgramArguments": program_args,
        "StandardOutPath": str(LOG_DIR / f"{cron_id}-stdout.log"),
        "StandardErrorPath": str(LOG_DIR / f"{cron_id}-stderr.log"),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:"
                    + str(Path.home() / ".local" / "bin") + ":"
                    + str(Path.home() / ".nvm/versions/node/v22.14.0/bin") + ":"
                    + "/Library/Frameworks/Python.framework/Versions/3.12/bin",
            "HOME": str(Path.home()),
            "NEXO_HOME": str(NEXO_HOME),
            "NEXO_CODE": str(RUNTIME_ROOT),
            "NEXO_SOURCE_CODE": str(SOURCE_ROOT),
            "NEXO_MANAGED_CORE_CRON": "1",
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
    if "interval_seconds" in cron and not cron.get("keep_alive"):
        plist["StartInterval"] = cron["interval_seconds"]
    elif "schedule" in cron and not cron.get("keep_alive"):
        cal = {}
        s = cron["schedule"]
        if "hour" in s:
            cal["Hour"] = s["hour"]
        if "minute" in s:
            cal["Minute"] = s["minute"]
        if "weekday" in s:
            cal["Weekday"] = s["weekday"]
        plist["StartCalendarInterval"] = cal

    return plist


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
    if existing.get("EnvironmentVariables") != new_plist.get("EnvironmentVariables"):
        return True
    return False


def install_plist(label: str, plist: dict, plist_path: Path, dry_run: bool):
    """Write plist and load it."""
    if dry_run:
        log(f"  DRY-RUN: would install {plist_path.name}")
        return

    # Unload if already loaded
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)

    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)

    subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True)
    log(f"  Installed + loaded: {plist_path.name}")


def unload_plist(plist_path: Path, dry_run: bool):
    """Unload and remove a plist."""
    if dry_run:
        log(f"  DRY-RUN: would remove {plist_path.name}")
        return

    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    plist_path.unlink(missing_ok=True)
    log(f"  Removed: {plist_path.name}")


def sync(dry_run: bool = False):
    system = platform.system()
    if system == "Linux":
        sync_linux(dry_run)
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
    #    (personal crons like shopify-backup, email-monitor are left alone)
    for cron_id, plist_path in installed.items():
        if cron_id not in manifest_ids:
            # Check if this was previously a core cron by reading the plist
            # If it points to NEXO_CODE scripts → it's core, safe to remove
            try:
                with open(plist_path, "rb") as f:
                    existing = plistlib.load(f)
                env = existing.get("EnvironmentVariables", {}) or {}
                args = existing.get("ProgramArguments", [])
                is_core = env.get("NEXO_MANAGED_CORE_CRON") == "1"
                if not is_core:
                    arg_blob = " ".join(str(a) for a in args)
                    is_core = (
                        "nexo-cron-wrapper.sh" in arg_blob
                        and (str(SOURCE_ROOT) in arg_blob or str(NEXO_HOME) in arg_blob)
                    )
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
    wrapper_src = SOURCE_ROOT / "scripts" / "nexo-cron-wrapper.sh"
    wrapper_dest = _copy_into_runtime(wrapper_src)

    log(f"Manifest: {len(manifest_crons)} core crons")

    python_bin = "/usr/bin/python3"
    for p in ["/usr/bin/python3", "/usr/local/bin/python3"]:
        if Path(p).exists():
            python_bin = p
            break

    for cron in manifest_crons:
        cron_id = cron["id"]
        script_src = SOURCE_ROOT / cron["script"]
        script_dest = _copy_into_runtime(script_src)
        script_type = cron.get("type", "python")

        # Copy subdirectories
        subdir_name = script_src.stem.replace("nexo-", "")
        subdir_src = SOURCE_ROOT / "scripts" / subdir_name
        if subdir_src.is_dir():
            _copy_into_runtime(subdir_src)

        if script_type == "shell":
            exec_cmd = f"/bin/bash {wrapper_dest} {cron_id} /bin/bash {script_dest}"
        else:
            exec_cmd = f"/bin/bash {wrapper_dest} {cron_id} {python_bin} {script_dest}"

        service_path = unit_dir / f"nexo-{cron_id}.service"
        timer_path = unit_dir / f"nexo-{cron_id}.timer"

        stdout_log = LOG_DIR / f"{cron_id}-stdout.log"
        stderr_log = LOG_DIR / f"{cron_id}-stderr.log"

        service_content = f"""[Unit]
Description=NEXO: {cron.get('description', cron_id)}

[Service]
Type=oneshot
ExecStart={exec_cmd}
Environment=NEXO_HOME={NEXO_HOME}
Environment=NEXO_CODE={SOURCE_ROOT}
Environment=HOME={Path.home()}
StandardOutput=append:{stdout_log}
StandardError=append:{stderr_log}
"""

        if cron.get("run_at_load"):
            timer_spec = "OnBootSec=0"
        elif "interval_seconds" in cron:
            timer_spec = f"OnUnitActiveSec={cron['interval_seconds']}s\nOnBootSec=60s"
        elif "schedule" in cron:
            s = cron["schedule"]
            h, m = s.get("hour", 0), s.get("minute", 0)
            if "weekday" in s:
                # Manifest weekday uses launchd convention: 0=Sunday … 6=Saturday (7=Sunday alias)
                days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                timer_spec = f"OnCalendar={days[s['weekday']]} *-*-* {h:02d}:{m:02d}:00"
            else:
                timer_spec = f"OnCalendar=*-*-* {h:02d}:{m:02d}:00"
        else:
            log(f"  SKIP {cron_id}: no schedule or interval")
            continue

        timer_content = f"""[Unit]
Description=NEXO timer: {cron.get('description', cron_id)}

[Timer]
{timer_spec}
Persistent=true

[Install]
WantedBy=timers.target
"""

        if dry_run:
            log(f"  DRY-RUN: would install {cron_id}")
            continue

        service_path.write_text(service_content)
        timer_path.write_text(timer_content)
        log(f"  Installed: {cron_id}")

    if not dry_run:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        for cron in manifest_crons:
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", f"nexo-{cron['id']}.timer"],
                capture_output=True
            )
        log("systemd timers enabled.")

    log("Sync complete.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        log("DRY RUN MODE — no changes will be made")
    sync(dry_run=dry_run)
