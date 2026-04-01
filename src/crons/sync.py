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
import subprocess
import sys
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent.parent)))
MANIFEST = Path(__file__).resolve().parent / "manifest.json"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LABEL_PREFIX = "com.nexo."
LOG_DIR = NEXO_HOME / "logs"


def log(msg: str):
    print(f"[cron-sync] {msg}", flush=True)


def load_manifest() -> list[dict]:
    with open(MANIFEST) as f:
        data = json.load(f)
    return data.get("crons", [])


def build_plist(cron: dict) -> dict:
    """Build a macOS LaunchAgent plist dict from a manifest entry."""
    cron_id = cron["id"]
    label = f"{LABEL_PREFIX}{cron_id}"
    script_path = str(NEXO_CODE / cron["script"])
    script_type = cron.get("type", "python")

    if script_type == "shell":
        program_args = ["/bin/bash", script_path]
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
        program_args = [python_bin, script_path]

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
            "NEXO_CODE": str(NEXO_CODE),
            "PYTHONUNBUFFERED": "1",
        },
    }

    # Schedule
    if "interval_seconds" in cron:
        plist["StartInterval"] = cron["interval_seconds"]
    elif "schedule" in cron:
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
    if platform.system() != "Darwin":
        log("Not macOS — cron sync only supports LaunchAgents. Skipping.")
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
                args = existing.get("ProgramArguments", [])
                is_core = any(str(NEXO_CODE) in str(a) for a in args)
            except Exception:
                is_core = False

            if is_core:
                log(f"  REMOVE (no longer in manifest): {cron_id}")
                unload_plist(plist_path, dry_run)
            else:
                log(f"  SKIP (personal): {cron_id}")

    log("Sync complete.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        log("DRY RUN MODE — no changes will be made")
    sync(dry_run=dry_run)
