"""Runtime tier checks — read-only health checks from existing artifacts. Target <5s."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from doctor.models import DoctorCheck

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

# Freshness thresholds in seconds
IMMUNE_FRESHNESS = 3600  # 1 hour (runs every 30 min)
WATCHDOG_FRESHNESS = 3600  # 1 hour (runs every 30 min)
CRON_STALE_THRESHOLD = 7200  # 2 hours for any cron


def _file_age_seconds(path: Path) -> float | None:
    """Return file age in seconds, or None if not found."""
    try:
        if path.is_file():
            return time.time() - path.stat().st_mtime
    except Exception:
        pass
    return None


def check_immune_status() -> DoctorCheck:
    """Check immune system status freshness."""
    status_file = NEXO_HOME / "coordination" / "immune-status.json"
    age = _file_age_seconds(status_file)

    if age is None:
        return DoctorCheck(
            id="runtime.immune_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary="Immune status file not found",
            evidence=[f"Expected: {status_file}"],
            repair_plan=["Check if immune cron is installed and running"],
            escalation_prompt="Immune system has never run or status file was deleted.",
        )

    age_min = age / 60
    if age > IMMUNE_FRESHNESS:
        return DoctorCheck(
            id="runtime.immune_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Immune status stale ({age_min:.0f} min old, threshold {IMMUNE_FRESHNESS // 60} min)",
            evidence=[f"{status_file} last modified {age_min:.0f} minutes ago"],
            repair_plan=[
                "Check LaunchAgent/systemd timer for immune cron",
                "nexo scripts call nexo_schedule_status --input '{}'",
            ],
            escalation_prompt="Investigate why immune system stopped refreshing.",
        )

    # Read status for additional context
    try:
        data = json.loads(status_file.read_text())
        overall = data.get("overall_status", "unknown")
        checks_count = len(data.get("checks", []))
        return DoctorCheck(
            id="runtime.immune_freshness",
            tier="runtime",
            status="healthy" if overall == "healthy" else "degraded",
            severity="info" if overall == "healthy" else "warn",
            summary=f"Immune: {overall} ({checks_count} checks, {age_min:.0f} min ago)",
        )
    except Exception:
        return DoctorCheck(
            id="runtime.immune_freshness",
            tier="runtime",
            status="healthy",
            severity="info",
            summary=f"Immune status fresh ({age_min:.0f} min ago)",
        )


def check_watchdog_status() -> DoctorCheck:
    """Check watchdog status freshness."""
    status_file = NEXO_HOME / "operations" / "watchdog-status.json"
    age = _file_age_seconds(status_file)

    if age is None:
        return DoctorCheck(
            id="runtime.watchdog_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary="Watchdog status file not found",
            evidence=[f"Expected: {status_file}"],
            repair_plan=["Check if watchdog cron is installed and running"],
            escalation_prompt="Watchdog has never run or status file was deleted.",
        )

    age_min = age / 60
    if age > WATCHDOG_FRESHNESS:
        return DoctorCheck(
            id="runtime.watchdog_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Watchdog status stale ({age_min:.0f} min old)",
            evidence=[
                f"{status_file} last modified {age_min:.0f} minutes ago",
                f"Expected freshness threshold: {WATCHDOG_FRESHNESS // 60} minutes",
            ],
            repair_plan=[
                "Inspect LaunchAgent or systemd timer for watchdog",
                "Check for macOS sandbox errors in stderr logs",
            ],
            escalation_prompt="Investigate why watchdog stopped refreshing despite timer being installed.",
        )

    # Read for detail
    try:
        data = json.loads(status_file.read_text())
        monitors = data.get("monitors_total", "?")
        passes = data.get("monitors_pass", "?")
        fails = data.get("monitors_fail", 0)
        status = "healthy" if fails == 0 else "degraded"
        return DoctorCheck(
            id="runtime.watchdog_freshness",
            tier="runtime",
            status=status,
            severity="info" if fails == 0 else "warn",
            summary=f"Watchdog: {passes}/{monitors} pass, {fails} fail ({age_min:.0f} min ago)",
        )
    except Exception:
        return DoctorCheck(
            id="runtime.watchdog_freshness",
            tier="runtime",
            status="healthy",
            severity="info",
            summary=f"Watchdog status fresh ({age_min:.0f} min ago)",
        )


def check_stale_sessions() -> DoctorCheck:
    """Check for stale sessions from DB."""
    try:
        import sqlite3
        db_path = NEXO_HOME / "data" / "nexo.db"
        if not db_path.is_file():
            return DoctorCheck(
                id="runtime.stale_sessions",
                tier="runtime",
                status="healthy",
                severity="info",
                summary="No DB to check sessions",
            )
        conn = sqlite3.connect(str(db_path), timeout=2)
        conn.row_factory = sqlite3.Row
        # Sessions older than 6 hours still active
        cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 21600))
        rows = conn.execute(
            "SELECT COUNT(*) as cnt FROM sessions WHERE status='active' AND started_at < ?",
            (cutoff,),
        ).fetchone()
        conn.close()
        count = rows["cnt"] if rows else 0
        if count > 0:
            return DoctorCheck(
                id="runtime.stale_sessions",
                tier="runtime",
                status="degraded",
                severity="warn",
                summary=f"{count} stale session{'s' if count > 1 else ''} (>6h old, still active)",
                repair_plan=["auto_close_sessions cron should handle this automatically"],
            )
        return DoctorCheck(
            id="runtime.stale_sessions",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="No stale sessions",
        )
    except Exception as e:
        return DoctorCheck(
            id="runtime.stale_sessions",
            tier="runtime",
            status="healthy",
            severity="info",
            summary=f"Session check skipped: {e}",
        )


def check_cron_freshness() -> DoctorCheck:
    """Check cron_runs table for recent executions."""
    try:
        import sqlite3
        db_path = NEXO_HOME / "data" / "nexo.db"
        if not db_path.is_file():
            return DoctorCheck(
                id="runtime.cron_freshness",
                tier="runtime",
                status="healthy",
                severity="info",
                summary="No DB to check cron runs",
            )
        conn = sqlite3.connect(str(db_path), timeout=2)
        # Check if cron_runs table exists
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cron_runs'"
        ).fetchone()
        if not tables:
            conn.close()
            return DoctorCheck(
                id="runtime.cron_freshness",
                tier="runtime",
                status="healthy",
                severity="info",
                summary="No cron_runs table yet",
            )
        # Latest run per cron
        rows = conn.execute(
            "SELECT cron_id, MAX(started_at) as last_run FROM cron_runs GROUP BY cron_id"
        ).fetchall()
        conn.close()

        stale = []
        now = time.time()
        for row in rows:
            try:
                last = time.mktime(time.strptime(row[1], "%Y-%m-%d %H:%M:%S"))
                if now - last > CRON_STALE_THRESHOLD:
                    stale.append(f"{row[0]}: {int((now - last) / 3600)}h ago")
            except Exception:
                pass

        if stale:
            return DoctorCheck(
                id="runtime.cron_freshness",
                tier="runtime",
                status="degraded",
                severity="warn",
                summary=f"{len(stale)} cron(s) haven't run recently",
                evidence=stale,
            )
        return DoctorCheck(
            id="runtime.cron_freshness",
            tier="runtime",
            status="healthy",
            severity="info",
            summary=f"All {len(rows)} tracked crons ran recently",
        )
    except Exception as e:
        return DoctorCheck(
            id="runtime.cron_freshness",
            tier="runtime",
            status="healthy",
            severity="info",
            summary=f"Cron check skipped: {e}",
        )


def run_runtime_checks(fix: bool = False) -> list[DoctorCheck]:
    """Run all runtime-tier checks. Read-only by default."""
    return [
        check_immune_status(),
        check_watchdog_status(),
        check_stale_sessions(),
        check_cron_freshness(),
    ]
