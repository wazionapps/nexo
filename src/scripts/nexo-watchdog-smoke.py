#!/usr/bin/env python3
"""
NEXO Watchdog Smoke

Runs the same health checks as the shell watchdog, but never restores files,
restarts services, disables evolution or notifies the user.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path


import sys


def _bootstrap_nexo_code(default_repo_src: Path) -> Path:
    nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    raw_env = os.environ.get("NEXO_CODE", "")
    candidates: list[Path] = []
    if raw_env:
        raw = Path(raw_env).expanduser()
        candidates.extend([raw, raw / "core"])
    candidates.extend([default_repo_src, nexo_home / "core", nexo_home])
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if (candidate / "paths.py").is_file() or (candidate / "server.py").is_file() or (candidate / "cli.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return candidate
    fallback = candidates[0]
    if str(fallback) not in sys.path:
        sys.path.insert(0, str(fallback))
    return fallback


NEXO_CODE = _bootstrap_nexo_code(Path(__file__).resolve().parents[1])

import paths

HOME = Path.home()
NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(HOME / ".nexo")))
BRAIN_DIR = paths.brain_dir()
LOG_DIR = paths.logs_dir()
SUMMARY_FILE = LOG_DIR / "watchdog-smoke-summary.json"
HASH_REGISTRY = paths.core_scripts_dir() / ".watchdog-hashes"
RESTORE_LOG = LOG_DIR / "snapshot-restores.log"


def _read_restore_count_for_current_hour() -> int:
    if not RESTORE_LOG.exists():
        return 0
    needle = datetime.now().strftime("%Y-%m-%d %H")
    return sum(1 for line in RESTORE_LOG.read_text(errors="ignore").splitlines()
               if needle in line and "/.codex/memories/nexo-" not in line)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    findings = []

    db_path = paths.db_path()
    integrity = "missing"
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path), timeout=10)
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            conn.close()
        except Exception as exc:
            integrity = f"error:{exc}"
    if integrity != "ok":
        findings.append({"severity": "ERROR", "area": "sqlite", "msg": f"integrity={integrity}"})

    # Check if the NEXO MCP server process is alive (replaces legacy cortex process check)
    nexo_server_running = subprocess.run(
        ["pgrep", "-f", "nexo-brain"],
        capture_output=True,
        text=True,
    ).returncode == 0
    if not nexo_server_running:
        findings.append({"severity": "INFO", "area": "server", "msg": "nexo-brain not running (normal if no active session)"})

    backups = sorted(paths.backups_dir().glob("nexo-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    if backups:
        age_seconds = int(datetime.now().timestamp() - backups[0].stat().st_mtime)
        if age_seconds > 7200:
            findings.append({"severity": "WARN", "area": "backup", "msg": f"latest backup age={age_seconds}s"})
    else:
        findings.append({"severity": "WARN", "area": "backup", "msg": "no backups found"})

    if HASH_REGISTRY.exists():
        for line in HASH_REGISTRY.read_text().splitlines():
            if not line.strip():
                continue
            filepath, expected_hash = line.split("|", 1)
            path = Path(filepath)
            if path.exists() and _sha256(path) != expected_hash:
                findings.append({"severity": "ERROR", "area": "immutable", "msg": f"hash mismatch: {filepath}"})

    restore_count = _read_restore_count_for_current_hour()
    if restore_count > 2:
        findings.append({"severity": "ERROR", "area": "restore_loop", "msg": f"{restore_count} restores this hour"})
    elif restore_count > 0:
        findings.append({"severity": "INFO", "area": "restore_activity", "msg": f"{restore_count} restores this hour"})

    # Check brain/ (canonical) first, fall back to cortex/ (legacy)
    objective = BRAIN_DIR / "evolution-objective.json"
    if not objective.exists():
        objective = NEXO_HOME / "cortex" / "evolution-objective.json"
    evolution_enabled = None
    if objective.exists():
        obj = json.loads(objective.read_text())
        evolution_enabled = obj.get("evolution_enabled", True)
        if not evolution_enabled:
            reason = str(obj.get("disabled_reason", "unknown"))
            findings.append({
                "severity": "WARN",
                "area": "evolution",
                "msg": f"disabled: {reason}",
            })

    summary = {
        "timestamp": datetime.now().isoformat(),
        "ok": not any(f["severity"] == "ERROR" for f in findings),
        "integrity": integrity,
        "server_running": nexo_server_running,
        "evolution_enabled": evolution_enabled,
        "restore_count_current_hour": restore_count,
        "findings": findings,
    }
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if any(f["severity"] == "ERROR" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
