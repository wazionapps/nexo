#!/usr/bin/env python3
"""
NEXO Watchdog Smoke

Runs the same health checks as the shell watchdog, but never restores files,
restarts services, disables evolution or notifies the user.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

HOME = Path.home()
CLAUDE_DIR = HOME / ".nexo"
NEXO_DIR = CLAUDE_DIR / "nexo-mcp"
CORTEX_DIR = CLAUDE_DIR / "cortex"
LOG_DIR = CLAUDE_DIR / "logs"
SUMMARY_FILE = LOG_DIR / "watchdog-smoke-summary.json"
HASH_REGISTRY = CLAUDE_DIR / "scripts" / ".watchdog-hashes"
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

    db_path = NEXO_DIR / "data" / "nexo.db"
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

    cortex_running = subprocess.run(
        ["pgrep", "-f", "cortex-wrapper.py"],
        capture_output=True,
        text=True,
    ).returncode == 0
    if not cortex_running:
        findings.append({"severity": "WARN", "area": "cortex", "msg": "cortex-wrapper.py not running"})

    backups = sorted((NEXO_DIR / "backups").glob("nexo-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
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

    objective = CORTEX_DIR / "evolution-objective.json"
    evolution_enabled = None
    if objective.exists():
        obj = json.loads(objective.read_text())
        evolution_enabled = obj.get("evolution_enabled", True)
        if not evolution_enabled:
            findings.append({
                "severity": "WARN",
                "area": "evolution",
                "msg": f"disabled: {obj.get('disabled_reason', 'unknown')}",
            })

    summary = {
        "timestamp": datetime.now().isoformat(),
        "ok": not any(f["severity"] == "ERROR" for f in findings),
        "integrity": integrity,
        "cortex_running": cortex_running,
        "evolution_enabled": evolution_enabled,
        "restore_count_current_hour": restore_count,
        "findings": findings,
    }
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if any(f["severity"] == "ERROR" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
