"""Runtime tier checks — read-only health checks from existing artifacts. Target <5s."""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

from doctor.models import DoctorCheck

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[2])))

# Freshness thresholds in seconds
IMMUNE_FRESHNESS = 3600  # 1 hour (runs every 30 min)
WATCHDOG_FRESHNESS = 3600  # 1 hour (runs every 30 min)
DEFAULT_CRON_THRESHOLD = 7200  # Fallback when manifest data is unavailable


def _file_age_seconds(path: Path) -> float | None:
    """Return file age in seconds, or None if not found."""
    try:
        if path.is_file():
            return time.time() - path.stat().st_mtime
    except Exception:
        pass
    return None


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _count_checks(checks) -> int:
    if isinstance(checks, list):
        return len(checks)
    if isinstance(checks, dict):
        total = 0
        for value in checks.values():
            if isinstance(value, list):
                total += len(value)
            elif value:
                total += 1
        return total
    return 0


def _parse_timestamp(value: str) -> dt.datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def _cron_expectations() -> dict[str, dict]:
    manifest_candidates = [
        NEXO_HOME / "crons" / "manifest.json",
        NEXO_CODE / "crons" / "manifest.json",
    ]
    for manifest_path in manifest_candidates:
        if not manifest_path.is_file():
            continue
        try:
            data = _load_json(manifest_path)
        except Exception:
            continue

        expectations = {}
        for cron in data.get("crons", []):
            cron_id = cron.get("id")
            if not cron_id or cron.get("run_at_load"):
                continue

            interval_seconds = cron.get("interval_seconds")
            schedule = cron.get("schedule") or {}
            if interval_seconds:
                threshold = max(int(interval_seconds) * 3, int(interval_seconds) + 600)
                label = f"every {int(interval_seconds) // 60}m"
            elif "weekday" in schedule:
                threshold = 8 * 86400
                label = "weekly"
            elif "hour" in schedule and "minute" in schedule:
                threshold = 36 * 3600
                label = "daily"
            else:
                threshold = DEFAULT_CRON_THRESHOLD
                label = "custom"

            expectations[cron_id] = {"threshold": threshold, "label": label}
        return expectations
    return {}


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
        data = _load_json(status_file)
        counts = data.get("counts") or {}
        ok_count = int(counts.get("OK", 0) or 0)
        warn_count = int(counts.get("WARN", 0) or 0)
        fail_count = int(counts.get("FAIL", 0) or 0)
        checks_count = _count_checks(data.get("checks"))
        if fail_count > 0:
            status = "critical"
            severity = "error"
            overall = "fail"
        elif warn_count > 0:
            status = "degraded"
            severity = "warn"
            overall = "warn"
        else:
            status = "healthy"
            severity = "info"
            overall = "ok"
        return DoctorCheck(
            id="runtime.immune_freshness",
            tier="runtime",
            status=status,
            severity=severity,
            summary=(
                f"Immune: {overall} "
                f"({ok_count} OK, {warn_count} WARN, {fail_count} FAIL; "
                f"{checks_count} checks, {age_min:.0f} min ago)"
            ),
        )
    except Exception as e:
        return DoctorCheck(
            id="runtime.immune_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Immune status unreadable ({age_min:.0f} min ago)",
            evidence=[str(e)],
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
        data = _load_json(status_file)
        summary = data.get("summary") or {}
        monitors = summary.get("total", "?")
        passes = summary.get("pass", "?")
        warns = int(summary.get("warn", 0) or 0)
        fails = int(summary.get("fail", 0) or 0)
        overall = str(summary.get("overall", "UNKNOWN")).upper()
        if overall == "FAIL" or fails > 0:
            status = "critical"
            severity = "error"
        elif overall == "WARN" or warns > 0:
            status = "degraded"
            severity = "warn"
        else:
            status = "healthy"
            severity = "info"
        return DoctorCheck(
            id="runtime.watchdog_freshness",
            tier="runtime",
            status=status,
            severity=severity,
            summary=(
                f"Watchdog: {passes}/{monitors} pass, {warns} warn, {fails} fail "
                f"({age_min:.0f} min ago)"
            ),
        )
    except Exception as e:
        return DoctorCheck(
            id="runtime.watchdog_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Watchdog status unreadable ({age_min:.0f} min ago)",
            evidence=[str(e)],
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
        cutoff = time.time() - 7200
        day_ago = time.time() - 86400
        rows = conn.execute(
            "SELECT COUNT(*) as cnt FROM sessions WHERE last_update_epoch < ? AND last_update_epoch > ?",
            (cutoff, day_ago),
        ).fetchone()
        conn.close()
        count = rows["cnt"] if rows else 0
        if count > 0:
            return DoctorCheck(
                id="runtime.stale_sessions",
                tier="runtime",
                status="degraded",
                severity="warn",
                summary=f"{count} stale session{'s' if count > 1 else ''} (no heartbeat >2h)",
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
            status="degraded",
            severity="warn",
            summary=f"Session check failed: {e}",
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
        expectations = _cron_expectations()
        now = time.time()
        for row in rows:
            cron_id = row[0]
            parsed = _parse_timestamp(row[1]) if row[1] else None
            if parsed is None:
                stale.append(f"{cron_id}: unreadable timestamp {row[1]!r}")
                continue

            age = now - parsed.timestamp()
            expected = expectations.get(cron_id, {"threshold": DEFAULT_CRON_THRESHOLD, "label": "runtime default"})
            if age > expected["threshold"]:
                stale.append(f"{cron_id}: {int(age / 3600)}h ago (expected {expected['label']})")

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
            status="degraded",
            severity="warn",
            summary=f"Cron check failed: {e}",
        )


def run_runtime_checks(fix: bool = False) -> list[DoctorCheck]:
    """Run all runtime-tier checks. Read-only by default."""
    return [
        check_immune_status(),
        check_watchdog_status(),
        check_stale_sessions(),
        check_cron_freshness(),
    ]
