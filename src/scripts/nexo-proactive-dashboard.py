#!/usr/bin/env python3
"""
NEXO Proactive Dashboard — Surfaces issues and opportunities without the user asking.

Scans: overdue followups, forgotten reminders, unresolved learnings,
inactive systems, user patterns, and more.

Usage:
    python3 nexo-proactive-dashboard.py           # Full scan, text output
    python3 nexo-proactive-dashboard.py --json     # JSON output for programmatic use
    python3 nexo-proactive-dashboard.py --brief    # One-liner alerts only
"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

NEXO_DB = NEXO_HOME / "data" / "nexo.db"


def get_db():
    conn = sqlite3.connect(str(NEXO_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def check_overdue_followups() -> list[dict]:
    """Find followups that are overdue and not completed."""
    conn = get_db()
    now_epoch = datetime.now().timestamp()
    rows = conn.execute("""
        SELECT id, description, date, created_at, reasoning
        FROM followups
        WHERE status NOT LIKE 'COMPLETED%'
        AND date IS NOT NULL AND date != ''
        ORDER BY date ASC
    """).fetchall()
    conn.close()
    alerts = []
    for r in rows:
        due_str = r["date"]
        try:
            due = datetime.fromisoformat(due_str) if due_str else None
            if due and due < datetime.now():
                days_overdue = (datetime.now() - due).days
                alerts.append({
                    "type": "overdue_followup",
                    "severity": "high" if days_overdue > 3 else "medium",
                    "title": f"Followup overdue by {days_overdue}d: {r['description'][:80]}",
                    "id": r["id"],
                    "days_overdue": days_overdue,
                })
        except (ValueError, TypeError):
            pass
    return alerts


def check_overdue_reminders() -> list[dict]:
    """Find reminders that are overdue."""
    conn = get_db()
    rows = conn.execute("""
        SELECT id, description, date, status
        FROM reminders
        WHERE status NOT IN ('COMPLETED', 'CANCELLED')
        AND date IS NOT NULL AND date != ''
        ORDER BY date ASC
    """).fetchall()
    conn.close()
    alerts = []
    for r in rows:
        due_str = r["date"]
        try:
            due = datetime.fromisoformat(due_str) if due_str else None
            if due and due < datetime.now():
                days_overdue = (datetime.now() - due).days
                alerts.append({
                    "type": "overdue_reminder",
                    "severity": "high" if days_overdue > 7 else "medium",
                    "title": f"Reminder overdue by {days_overdue}d: {r['description'][:80]}",
                    "id": r["id"],
                    "days_overdue": days_overdue,
                })
        except (ValueError, TypeError):
            pass
    return alerts


def check_stale_ideas() -> list[dict]:
    """Find reminders/ideas without due dates that have been sitting for too long."""
    conn = get_db()
    rows = conn.execute("""
        SELECT id, description, created_at
        FROM reminders
        WHERE status NOT IN ('COMPLETED', 'CANCELLED')
        AND (date IS NULL OR date = '')
        ORDER BY created_at ASC
    """).fetchall()
    conn.close()
    alerts = []
    stale_count = 0
    for r in rows:
        try:
            # created_at is epoch float
            created = datetime.fromtimestamp(r["created_at"])
            age_days = (datetime.now() - created).days
        except (ValueError, TypeError, OSError):
            age_days = 0
        if age_days > 14:
            stale_count += 1

    if stale_count > 10:
        alerts.append({
            "type": "stale_ideas",
            "severity": "low",
            "title": f"{stale_count} ideas/reminders without date have been sitting for >14 days. Review or archive.",
            "count": stale_count,
        })
    return alerts


def check_session_gaps() -> list[dict]:
    """Detect if NEXO hasn't been active for unusual periods."""
    conn = get_db()
    row = conn.execute("""
        SELECT MAX(created_at) as last_diary FROM session_diary
    """).fetchone()
    conn.close()
    alerts = []
    if row and row["last_diary"]:
        try:
            last = datetime.fromisoformat(row["last_diary"])
            gap_hours = (datetime.now() - last).total_seconds() / 3600
            if gap_hours > 48:
                alerts.append({
                    "type": "session_gap",
                    "severity": "low",
                    "title": f"No sessions recorded in {gap_hours:.0f}h ({gap_hours/24:.1f} days)",
                    "gap_hours": gap_hours,
                })
        except (ValueError, TypeError):
            pass
    return alerts


def check_evolution_status() -> list[dict]:
    """Check if evolution system is healthy."""
    alerts = []
    obj_file = NEXO_HOME / "cortex" / "evolution-objective.json"
    if obj_file.exists():
        obj = json.loads(obj_file.read_text())
        if not obj.get("evolution_enabled", True):
            alerts.append({
                "type": "evolution_disabled",
                "severity": "high",
                "title": f"Evolution DISABLED: {obj.get('disabled_reason', 'unknown')}",
            })
        if obj.get("consecutive_failures", 0) > 0:
            alerts.append({
                "type": "evolution_failures",
                "severity": "medium",
                "title": f"Evolution: {obj['consecutive_failures']} consecutive failures",
            })

        # Check dimension regression
        for dim, data in obj.get("dimensions", {}).items():
            current = data.get("current", 0)
            if current < 30:
                alerts.append({
                    "type": "dimension_low",
                    "severity": "medium",
                    "title": f"Dimension '{dim}' baja: {current}%",
                    "dimension": dim,
                    "score": current,
                })
    return alerts


def check_pending_proposals() -> list[dict]:
    """Check for evolution proposals awaiting the user's review."""
    conn = get_db()
    rows = conn.execute("""
        SELECT id, dimension, proposal, created_at
        FROM evolution_log
        WHERE status = 'proposed' AND classification = 'propose'
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    if rows:
        return [{
            "type": "pending_proposals",
            "severity": "low",
            "title": f"{len(rows)} evolution proposals pending review",
            "count": len(rows),
            "proposals": [{"id": r["id"], "dim": r["dimension"], "text": r["proposal"][:80]} for r in rows],
        }]
    return []


def check_recurring_errors() -> list[dict]:
    """Detect learnings that keep appearing (same issue reported multiple times)."""
    conn = get_db()
    rows = conn.execute("""
        SELECT category, COUNT(*) as cnt
        FROM learnings
        WHERE created_at > datetime('now', '-7 days')
        GROUP BY category
        HAVING cnt >= 5
        ORDER BY cnt DESC
    """).fetchall()
    conn.close()
    alerts = []
    for r in rows:
        alerts.append({
            "type": "recurring_errors",
            "severity": "medium",
            "title": f"Category '{r['category']}' has {r['cnt']} learnings this week — possible systemic issue",
            "category": r["category"],
            "count": r["cnt"],
        })
    return alerts


def check_cron_health() -> list[dict]:
    """Check if critical cron jobs are running."""
    alerts = []

    # Check backup cron
    backup_dir = NEXO_HOME / "backups"
    if backup_dir.exists():
        backups = sorted(backup_dir.glob("nexo-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        if backups:
            last_backup_age = (datetime.now().timestamp() - backups[0].stat().st_mtime) / 3600
            if last_backup_age > 4:
                alerts.append({
                    "type": "backup_stale",
                    "severity": "high",
                    "title": f"Last nexo.db backup {last_backup_age:.1f}h (should be hourly)",
                })

    # Check immune system
    immune_status = NEXO_HOME / "coordination" / "immune-status.json"
    if immune_status.exists():
        try:
            status = json.loads(immune_status.read_text())
            if status.get("status") == "degraded":
                alerts.append({
                    "type": "immune_degraded",
                    "severity": "high",
                    "title": f"Immune system degraded: {status.get('reason', '?')}",
                })
        except (json.JSONDecodeError, KeyError):
            pass

    return alerts


def run_all_checks() -> list[dict]:
    """Run all proactive checks and return sorted alerts."""
    all_alerts = []
    checks = [
        check_overdue_followups,
        check_overdue_reminders,
        check_stale_ideas,
        check_session_gaps,
        check_evolution_status,
        check_pending_proposals,
        check_recurring_errors,
        check_cron_health,
    ]

    for check in checks:
        try:
            all_alerts.extend(check())
        except Exception as e:
            all_alerts.append({
                "type": "check_error",
                "severity": "low",
                "title": f"Check {check.__name__} failed: {e}",
            })

    # Sort by severity
    severity_order = {"high": 0, "medium": 1, "low": 2}
    all_alerts.sort(key=lambda a: severity_order.get(a.get("severity", "low"), 3))

    return all_alerts


def format_text(alerts: list[dict]) -> str:
    """Format alerts as readable text."""
    if not alerts:
        return "No proactive alerts. All clear."

    severity_icons = {"high": "!!!", "medium": " ! ", "low": " . "}
    lines = [f"NEXO Proactive Dashboard — {len(alerts)} alerts\n"]

    current_severity = None
    for a in alerts:
        sev = a.get("severity", "low")
        if sev != current_severity:
            current_severity = sev
            label = {"high": "URGENTE", "medium": "ATENCION", "low": "INFO"}.get(sev, sev)
            lines.append(f"\n  [{label}]")
        icon = severity_icons.get(sev, " . ")
        lines.append(f"  {icon} {a['title']}")

    return "\n".join(lines)


def format_brief(alerts: list[dict]) -> str:
    """One-liner summary."""
    high = sum(1 for a in alerts if a.get("severity") == "high")
    med = sum(1 for a in alerts if a.get("severity") == "medium")
    low = sum(1 for a in alerts if a.get("severity") == "low")
    if not alerts:
        return "Dashboard: clean"
    return f"Dashboard: {high} urgent, {med} attention, {low} info"


def main():
    output_json = "--json" in sys.argv
    brief = "--brief" in sys.argv

    alerts = run_all_checks()

    if output_json:
        print(json.dumps(alerts, indent=2, default=str))
    elif brief:
        print(format_brief(alerts))
    else:
        print(format_text(alerts))

    # Exit code = number of high severity alerts
    high_count = sum(1 for a in alerts if a.get("severity") == "high")
    sys.exit(min(high_count, 125))


if __name__ == "__main__":
    main()
