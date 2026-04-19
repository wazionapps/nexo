#!/usr/bin/env python3
"""
NEXO Auto-Update — Daily automatic update of Brain + runtime dependencies.

Runs once daily via LaunchAgent. Executes the same update flow as `nexo update`:
- Brain itself (git pull or npm update)
- Runtime dependencies declared in package.json runtimeDependencies

Zero interaction required. Logs results to NEXO_HOME/logs/auto-update.log.
Idempotent — safe to run multiple times.
"""

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from paths import data_dir, logs_dir

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

LOG_DIR = logs_dir()
LOG_FILE = LOG_DIR / "auto-update.log"
LOCK_FILE = data_dir() / "auto-update.lock"
MAX_LOG_SIZE = 512 * 1024  # 512 KB


def _log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Rotate if too large
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_SIZE:
        rotated = LOG_FILE.with_suffix(".log.1")
        try:
            LOG_FILE.rename(rotated)
        except Exception:
            pass
    with open(LOG_FILE, "a") as f:
        f.write(line)


def _acquire_lock():
    """Acquire an exclusive lock to prevent concurrent updates."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except (IOError, OSError):
        lock_fd.close()
        return None


def main():
    lock_fd = _acquire_lock()
    if lock_fd is None:
        _log("Skipped: another auto-update is already running.")
        return 0

    try:
        _log("Auto-update started.")

        # Use `nexo update --json` via CLI for the full update flow
        nexo_bin = NEXO_HOME / "bin" / "nexo"
        if not nexo_bin.exists():
            # Try the npm global bin
            try:
                result = subprocess.run(
                    ["which", "nexo"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    nexo_bin = Path(result.stdout.strip())
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass

        if not nexo_bin.exists():
            _log("ERROR: nexo CLI not found. Cannot auto-update.")
            return 1

        try:
            result = subprocess.run(
                [str(nexo_bin), "update", "--json"],
                capture_output=True, text=True,
                timeout=300,  # 5 minutes max
                env={**os.environ, "NEXO_HOME": str(NEXO_HOME), "NEXO_CODE": str(NEXO_CODE)},
            )
        except subprocess.TimeoutExpired:
            _log("ERROR: Auto-update timed out after 300s.")
            return 1

        if result.returncode != 0:
            _log(f"ERROR: nexo update failed (exit {result.returncode}): {result.stderr or result.stdout}")
            return 1

        # Parse and log results
        try:
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            _log(f"Update completed but output was not JSON: {result.stdout[:500]}")
            return 0

        # Log Brain update status
        mode = data.get("mode", "unknown")
        if "Already up to date" in str(data.get("message", "")):
            _log(f"Brain: already up to date ({mode} mode).")
        else:
            version_info = ""
            if data.get("version"):
                version_info = f" v{data['version']}"
            _log(f"Brain: updated{version_info} ({mode} mode).")

        # Log runtime dependency results
        deps = data.get("runtime_dependencies") or []
        for dep in deps:
            name = dep.get("name", "")
            status = dep.get("status", "")
            if status == "updated":
                _log(f"Dependency: {name} {dep.get('old_version')} -> {dep.get('new_version')}")
            elif status == "installed":
                _log(f"Dependency: {name} installed ({dep.get('new_version')})")
            elif status == "already_latest":
                _log(f"Dependency: {name} {dep.get('old_version')} (latest)")
            elif status == "failed":
                _log(f"Dependency WARNING: {name} failed: {dep.get('error', 'unknown')}")

        _log("Auto-update completed successfully.")
        return 0

    except Exception as e:
        _log(f"ERROR: Unexpected error: {e}")
        return 1
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
