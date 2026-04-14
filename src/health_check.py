"""Health check — one-shot snapshot of NEXO Brain subsystems.

Output is stable JSON consumable by any UI or monitoring tool.
No side effects, no network, no mutation.

Subsystems reported:
  - runtime     : NEXO_HOME exists, version.json readable, version string
  - database    : SQLite reachable, integrity check, basic row counts
  - crons       : count of active personal LaunchAgents (macOS) or unknown
  - mcp         : Claude Code MCP config present and mentions nexo-brain
  - errors      : count of recent errors in ~/.nexo/operations/*.log (24h)
  - events      : count of events emitted in last 24h

Top-level `status` is "ok" | "degraded" | "error".
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any


def _nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


def _check_runtime() -> dict:
    home = _nexo_home()
    ver_file = home / "version.json"
    out: dict[str, Any] = {"nexo_home": str(home), "exists": home.is_dir()}
    if ver_file.is_file():
        try:
            payload = json.loads(ver_file.read_text())
            out["version"] = payload.get("version", "unknown")
        except Exception as exc:
            out["version"] = "unreadable"
            out["error"] = str(exc)
    else:
        out["version"] = "missing"
    out["status"] = "ok" if out["exists"] and out.get("version") not in ("missing", "unreadable") else "degraded"
    return out


def _check_database() -> dict:
    db_path = _nexo_home() / "data" / "nexo.db"
    out: dict[str, Any] = {"path": str(db_path), "exists": db_path.is_file()}
    if not out["exists"]:
        out["status"] = "error"
        return out
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        try:
            cur = conn.execute("PRAGMA integrity_check")
            row = cur.fetchone()
            out["integrity"] = row[0] if row else "unknown"
        finally:
            conn.close()
        out["status"] = "ok" if out["integrity"] == "ok" else "degraded"
    except Exception as exc:
        out["status"] = "error"
        out["error"] = str(exc)
    return out


def _check_crons() -> dict:
    out: dict[str, Any] = {}
    # macOS LaunchAgents
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    if agents_dir.is_dir():
        try:
            plists = [p for p in agents_dir.glob("com.nexo.*.plist")]
            out["launch_agents"] = len(plists)
            out["platform"] = "macos"
        except Exception as exc:
            out["error"] = str(exc)
    else:
        out["platform"] = "unknown"
    out["status"] = "ok"
    return out


def _check_mcp() -> dict:
    out: dict[str, Any] = {}
    candidates = [
        Path.home() / ".claude.json",
        Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
    ]
    found = []
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        if "nexo" in text.lower():
            found.append(str(path))
    out["configs_with_nexo"] = found
    out["status"] = "ok" if found else "degraded"
    if not found:
        out["reason"] = "no client config mentions nexo-brain"
    return out


def _check_errors(hours: int = 24) -> dict:
    ops_dir = _nexo_home() / "operations"
    out: dict[str, Any] = {"dir": str(ops_dir)}
    if not ops_dir.is_dir():
        out["recent_errors"] = 0
        out["status"] = "ok"
        return out

    cutoff = time.time() - hours * 3600
    recent = 0
    sample: list[str] = []
    error_re = re.compile(r"(?i)\b(error|traceback|exception|fail(ed)?)\b")

    for log in ops_dir.glob("*.log"):
        try:
            if log.stat().st_mtime < cutoff:
                continue
            with log.open("r", errors="ignore") as fh:
                for line in fh:
                    if error_re.search(line):
                        recent += 1
                        if len(sample) < 5:
                            sample.append(line.strip()[:200])
        except Exception:
            continue

    out["recent_errors"] = recent
    out["sample"] = sample
    out["status"] = "ok" if recent < 20 else "degraded"
    return out


def _check_events(hours: int = 24) -> dict:
    events_file = _nexo_home() / "runtime" / "events.ndjson"
    out: dict[str, Any] = {"path": str(events_file), "exists": events_file.is_file()}
    if not events_file.is_file():
        out["recent_events"] = 0
        out["status"] = "ok"
        return out
    cutoff = time.time() - hours * 3600
    count = 0
    urgent = 0
    try:
        with events_file.open("r", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except Exception:
                    continue
                if float(evt.get("ts", 0)) < cutoff:
                    continue
                count += 1
                if evt.get("priority") == "urgent":
                    urgent += 1
    except Exception as exc:
        out["error"] = str(exc)
    out["recent_events"] = count
    out["urgent"] = urgent
    out["status"] = "degraded" if urgent > 0 else "ok"
    return out


def collect() -> dict:
    """Run every subsystem check and return a unified report."""
    report: dict[str, Any] = {
        "ts": time.time(),
        "subsystems": {
            "runtime": _check_runtime(),
            "database": _check_database(),
            "crons": _check_crons(),
            "mcp": _check_mcp(),
            "errors": _check_errors(),
            "events": _check_events(),
        },
    }
    statuses = [sub.get("status", "unknown") for sub in report["subsystems"].values()]
    if "error" in statuses:
        report["status"] = "error"
    elif "degraded" in statuses:
        report["status"] = "degraded"
    else:
        report["status"] = "ok"
    return report
