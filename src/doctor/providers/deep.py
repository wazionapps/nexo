"""Deep tier checks — read existing artifacts for richer validation. Target <60s."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from doctor.models import DoctorCheck

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

# Freshness thresholds
SELF_AUDIT_FRESHNESS = 86400 * 2  # 2 days (runs daily)
PREFLIGHT_FRESHNESS = 86400  # 1 day
WATCHDOG_SMOKE_FRESHNESS = 86400  # 1 day


def _file_age_seconds(path: Path) -> float | None:
    try:
        if path.is_file():
            return time.time() - path.stat().st_mtime
    except Exception:
        pass
    return None


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def check_self_audit_summary() -> DoctorCheck:
    """Check latest self-audit summary exists and is recent."""
    summary_file = NEXO_HOME / "logs" / "self-audit-summary.json"
    age = _file_age_seconds(summary_file)

    if age is None:
        return DoctorCheck(
            id="deep.self_audit",
            tier="deep",
            status="degraded",
            severity="warn",
            summary="Self-audit summary not found",
            evidence=[f"Expected: {summary_file}"],
            repair_plan=["Check if daily self-audit cron is installed"],
        )

    age_hours = age / 3600
    if age > SELF_AUDIT_FRESHNESS:
        return DoctorCheck(
            id="deep.self_audit",
            tier="deep",
            status="degraded",
            severity="warn",
            summary=f"Self-audit summary stale ({age_hours:.0f}h old)",
            evidence=[f"Last modified {age_hours:.0f} hours ago, threshold {SELF_AUDIT_FRESHNESS // 3600}h"],
        )

    try:
        data = _load_json(summary_file)
        counts = data.get("counts") or {}
        error_count = int(counts.get("error", 0) or 0)
        warn_count = int(counts.get("warn", 0) or 0)
        findings = data.get("findings") or []
        if error_count > 0:
            status = "critical"
            severity = "error"
        elif warn_count > 0:
            status = "degraded"
            severity = "warn"
        else:
            status = "healthy"
            severity = "info"
        return DoctorCheck(
            id="deep.self_audit",
            tier="deep",
            status=status,
            severity=severity,
            summary=(
                f"Self-audit: {len(findings)} findings "
                f"({error_count} error, {warn_count} warn; {age_hours:.0f}h ago)"
            ),
        )
    except Exception as e:
        return DoctorCheck(
            id="deep.self_audit",
            tier="deep",
            status="degraded",
            severity="warn",
            summary=f"Self-audit summary unreadable ({age_hours:.0f}h ago)",
            evidence=[str(e)],
        )


def check_schema_version() -> DoctorCheck:
    """Check DB schema version is present and reasonable."""
    try:
        import sqlite3
        db_path = NEXO_HOME / "data" / "nexo.db"
        if not db_path.is_file():
            return DoctorCheck(
                id="deep.schema_version",
                tier="deep",
                status="degraded",
                severity="warn",
                summary="No database to check schema",
            )
        conn = sqlite3.connect(str(db_path), timeout=2)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        return DoctorCheck(
            id="deep.schema_version",
            tier="deep",
            status="healthy",
            severity="info",
            summary=f"DB schema version: {version}",
        )
    except Exception as e:
        return DoctorCheck(
            id="deep.schema_version",
            tier="deep",
            status="degraded",
            severity="warn",
            summary=f"Schema check failed: {e}",
        )


def check_preflight_summary() -> DoctorCheck:
    """Check runtime preflight summary."""
    summary_file = NEXO_HOME / "logs" / "runtime-preflight-summary.json"
    age = _file_age_seconds(summary_file)

    if age is None:
        return DoctorCheck(
            id="deep.preflight",
            tier="deep",
            status="healthy",
            severity="info",
            summary="No preflight summary (optional)",
        )

    age_hours = age / 3600
    if age > PREFLIGHT_FRESHNESS:
        return DoctorCheck(
            id="deep.preflight",
            tier="deep",
            status="degraded",
            severity="warn",
            summary=f"Preflight summary stale ({age_hours:.0f}h old)",
        )
    try:
        data = _load_json(summary_file)
        ok = data.get("ok")
        checks = data.get("checks") or {}
        errors = data.get("errors") or []
        if ok is True:
            return DoctorCheck(
                id="deep.preflight",
                tier="deep",
                status="healthy",
                severity="info",
                summary=f"Runtime preflight OK ({len(checks)} checks, {age_hours:.0f}h ago)",
            )
        return DoctorCheck(
            id="deep.preflight",
            tier="deep",
            status="critical",
            severity="error",
            summary=f"Runtime preflight failed ({len(errors)} errors, {age_hours:.0f}h ago)",
            evidence=errors[:5],
        )
    except Exception as e:
        return DoctorCheck(
            id="deep.preflight",
            tier="deep",
            status="degraded",
            severity="warn",
            summary=f"Preflight summary unreadable ({age_hours:.0f}h ago)",
            evidence=[str(e)],
        )


def check_watchdog_smoke() -> DoctorCheck:
    """Check watchdog smoke summary."""
    summary_file = NEXO_HOME / "logs" / "watchdog-smoke-summary.json"
    age = _file_age_seconds(summary_file)

    if age is None:
        return DoctorCheck(
            id="deep.watchdog_smoke",
            tier="deep",
            status="healthy",
            severity="info",
            summary="No watchdog smoke summary (optional)",
        )

    age_hours = age / 3600
    if age > WATCHDOG_SMOKE_FRESHNESS:
        return DoctorCheck(
            id="deep.watchdog_smoke",
            tier="deep",
            status="degraded",
            severity="warn",
            summary=f"Watchdog smoke summary stale ({age_hours:.0f}h old)",
        )

    try:
        data = _load_json(summary_file)
        ok = data.get("ok")
        findings = data.get("findings") or []
        error_count = sum(1 for finding in findings if finding.get("severity") == "ERROR")
        if ok is True:
            return DoctorCheck(
                id="deep.watchdog_smoke",
                tier="deep",
                status="healthy",
                severity="info",
                summary=f"Watchdog smoke OK ({len(findings)} findings, {age_hours:.0f}h ago)",
            )
        return DoctorCheck(
            id="deep.watchdog_smoke",
            tier="deep",
            status="critical",
            severity="error",
            summary=f"Watchdog smoke failed ({error_count} errors, {age_hours:.0f}h ago)",
            evidence=[finding.get("msg", "") for finding in findings[:5]],
        )
    except Exception as e:
        return DoctorCheck(
            id="deep.watchdog_smoke",
            tier="deep",
            status="degraded",
            severity="warn",
            summary=f"Watchdog smoke summary unreadable ({age_hours:.0f}h ago)",
            evidence=[str(e)],
        )


def check_learning_count() -> DoctorCheck:
    """Check learning count as a health proxy."""
    try:
        import sqlite3
        db_path = NEXO_HOME / "data" / "nexo.db"
        if not db_path.is_file():
            return DoctorCheck(
                id="deep.learning_count",
                tier="deep",
                status="healthy",
                severity="info",
                summary="No DB to check learnings",
            )
        conn = sqlite3.connect(str(db_path), timeout=2)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='learnings'"
        ).fetchone()
        if not tables:
            conn.close()
            return DoctorCheck(
                id="deep.learning_count",
                tier="deep",
                status="healthy",
                severity="info",
                summary="No learnings table yet",
            )
        count = conn.execute("SELECT COUNT(*) FROM learnings WHERE archived=0").fetchone()[0]
        conn.close()
        return DoctorCheck(
            id="deep.learning_count",
            tier="deep",
            status="healthy",
            severity="info",
            summary=f"{count} active learnings in memory",
        )
    except Exception as e:
        return DoctorCheck(
            id="deep.learning_count",
            tier="deep",
            status="healthy",
            severity="info",
            summary=f"Learning check skipped: {e}",
        )


def run_deep_checks(fix: bool = False) -> list[DoctorCheck]:
    """Run all deep-tier checks. Read-only."""
    return [
        check_self_audit_summary(),
        check_schema_version(),
        check_preflight_summary(),
        check_watchdog_smoke(),
        check_learning_count(),
    ]
