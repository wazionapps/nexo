#!/usr/bin/env python3
# nexo: name=runner-health-check
# nexo: description=Watchdog check: verifica que runners (followup-runner, morning-agent) producen trabajo real. Alerta si 48h sin ejecución o sin output útil.
# nexo: category=watchdog
# nexo: runtime=python
# nexo: timeout=60
# nexo: cron_id=runner-health-check
# nexo: interval_seconds=21600
# nexo: schedule_required=true
# nexo: recovery_policy=catchup
# nexo: run_on_boot=true
# nexo: idempotent=true
# nexo: max_catchup_age=43200
# nexo: doctor_allow_db=true

"""
Runner Health Check — verifica que los runners NEXO producen trabajo real.

Checks:
1. followup-runner: ¿ha ejecutado en las últimas 48h? ¿Ha cambiado estado de algún followup?
2. morning-agent: ¿ha ejecutado exitosamente en las últimas 48h?
3. Log de resultados: ¿los logs no están vacíos?
4. Minimum execution count: ¿al menos N ejecuciones en la última semana?

Output: JSON report + alerta en .watchdog-alert si hay problemas.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Resolve NEXO_HOME + inject src/ (repo or ~/.nexo/core) into sys.path so
# ``from paths import ...`` works in both installed (core) and in-repo
# (checkout) layouts.
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
if str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

from paths import db_path, logs_dir, operations_dir

DB_PATH = db_path()
OPS_DIR = operations_dir()
LOG_DIR = logs_dir()
REPORT_PATH = OPS_DIR / "runner-health-report.json"
ALERT_FILE = OPS_DIR / ".watchdog-alert"

# Thresholds
MAX_HOURS_NO_RUN = 48
MIN_WEEKLY_RUNS = 3  # At least 3 successful runs per week per runner
RUNNERS = [
    {
        "cron_id": "followup-runner",
        "name": "Followup Runner",
        "stdout_log": LOG_DIR / "followup-runner-stdout.log",
        "activity_log": LOG_DIR / "followup-runner.log",
        "min_weekly": MIN_WEEKLY_RUNS,
    },
    {
        "cron_id": "morning-agent",
        "name": "Morning Agent",
        "stdout_log": LOG_DIR / "morning-agent-stdout.log",
        "min_weekly": MIN_WEEKLY_RUNS,
    },
]


def _recent_summary_evidence(conn: sqlite3.Connection, cron_id: str, cutoff: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT summary, started_at FROM cron_runs WHERE cron_id=? AND started_at > ? AND summary != '' ORDER BY started_at DESC LIMIT 1",
        (cron_id, cutoff),
    ).fetchone()
    if not row:
        return None
    return {
        "summary": row[0][:200],
        "started_at": row[1],
    }


def _recent_log_evidence(now: datetime, max_age_hours: int, *sources: tuple[str, Optional[Path]]) -> tuple[Optional[dict], list[str]]:
    issues: list[str] = []
    for label, path in sources:
        if not path:
            continue
        if not path.exists():
            issues.append(f"{label} file not found")
            continue

        stat = path.stat()
        age_hours = (now.timestamp() - stat.st_mtime) / 3600
        if stat.st_size == 0:
            issues.append(f"{label} is empty")
            continue
        if age_hours > max_age_hours:
            issues.append(f"{label} is {age_hours:.0f}h old")
            continue

        return {
            "log_source": label,
            "log_path": str(path),
            "log_age_hours": round(age_hours, 1),
            "log_size_bytes": stat.st_size,
        }, issues

    return None, issues


def _last_error_state(conn: sqlite3.Connection, cron_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT error, started_at FROM cron_runs WHERE cron_id=? AND error != '' AND error IS NOT NULL ORDER BY started_at DESC LIMIT 1",
        (cron_id,),
    ).fetchone()
    if not row:
        return None

    successful_since = conn.execute(
        "SELECT COUNT(*) FROM cron_runs WHERE cron_id=? AND started_at > ? AND (exit_code=0 OR exit_code IS NULL)",
        (cron_id, row[1]),
    ).fetchone()
    age_row = conn.execute(
        "SELECT ROUND((julianday('now') - julianday(?)) * 24, 1)",
        (row[1],),
    ).fetchone()

    return {
        "last_error": row[0][:200],
        "last_error_at": row[1],
        "last_error_age_hours": age_row[0] if age_row else None,
        "successful_runs_since_last_error": successful_since[0] if successful_since else 0,
    }


def check_runner(conn: sqlite3.Connection, runner: dict) -> dict:
    cron_id = runner["cron_id"]
    now = datetime.now(timezone.utc)
    cutoff_48h = (now - timedelta(hours=MAX_HOURS_NO_RUN)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_7d = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    result = {
        "cron_id": cron_id,
        "name": runner["name"],
        "status": "PASS",
        "issues": [],
    }

    # Check 1: Any run in the last 48h?
    row = conn.execute(
        "SELECT COUNT(*), MAX(started_at) FROM cron_runs WHERE cron_id=? AND started_at > ?",
        (cron_id, cutoff_48h),
    ).fetchone()
    runs_48h = row[0] or 0
    last_run = row[1] or "never"

    result["last_run"] = last_run
    result["runs_last_48h"] = runs_48h

    if runs_48h == 0:
        result["status"] = "FAIL"
        result["issues"].append(f"No runs in the last {MAX_HOURS_NO_RUN}h (last: {last_run})")

    # Check 2: Successful runs in the last week
    row = conn.execute(
        "SELECT COUNT(*) FROM cron_runs WHERE cron_id=? AND started_at > ? AND (exit_code=0 OR exit_code IS NULL)",
        (cron_id, cutoff_7d),
    ).fetchone()
    success_7d = row[0] or 0
    result["successful_runs_last_7d"] = success_7d

    if success_7d < runner["min_weekly"]:
        severity = "FAIL" if success_7d == 0 else "WARN"
        if result["status"] != "FAIL":
            result["status"] = severity
        result["issues"].append(
            f"Only {success_7d} successful runs in last 7d (min: {runner['min_weekly']})"
        )

    # Check 3: Error rate in last week
    row = conn.execute(
        "SELECT COUNT(*) FROM cron_runs WHERE cron_id=? AND started_at > ? AND exit_code IS NOT NULL AND exit_code != 0",
        (cron_id, cutoff_7d),
    ).fetchone()
    errors_7d = row[0] or 0
    total_7d = success_7d + errors_7d
    result["errors_last_7d"] = errors_7d
    result["total_runs_last_7d"] = total_7d

    error_state = _last_error_state(conn, cron_id)
    if error_state:
        result.update(error_state)

    if total_7d > 0 and errors_7d / total_7d > 0.5:
        recovered_cleanly = (
            error_state is not None
            and error_state.get("last_error_age_hours") is not None
            and error_state["last_error_age_hours"] >= 24
            and error_state["successful_runs_since_last_error"] >= 2
        )
        if recovered_cleanly:
            result["historical_error_rate_note"] = (
                f"Suppressed weekly error-rate warning after recovery: "
                f"{errors_7d}/{total_7d} failed in 7d, but last error is "
                f"{error_state['last_error_age_hours']:.1f}h old and "
                f"{error_state['successful_runs_since_last_error']} runs succeeded since."
            )
        else:
            if result["status"] != "FAIL":
                result["status"] = "WARN"
            result["issues"].append(
                f"High error rate: {errors_7d}/{total_7d} runs failed in last 7d"
            )

    # Check 5: Recent log evidence
    log_evidence, log_issues = _recent_log_evidence(
        now,
        MAX_HOURS_NO_RUN,
        ("stdout log", runner.get("stdout_log")),
        ("activity log", runner.get("activity_log")),
    )
    if log_evidence:
        result.update(log_evidence)
    else:
        fallback = _recent_summary_evidence(conn, cron_id, cutoff_48h)
        if fallback:
            result["log_source"] = "cron_runs summary"
            result["log_summary"] = fallback["summary"]
            result["log_summary_at"] = fallback["started_at"]
        else:
            if result["status"] != "FAIL":
                result["status"] = "WARN"
            detail = "; ".join(log_issues[:2]) if log_issues else "no recent log evidence"
            result["issues"].append(f"no recent log evidence ({detail})")

    # Check 6: For followup-runner specifically — check if followups change state
    if cron_id == "followup-runner":
        row = conn.execute(
            "SELECT COUNT(*) FROM followups WHERE status != 'PENDING' AND updated_at > ?",
            (cutoff_7d,),
        ).fetchone()
        # updated_at is epoch float
        recent_updated = conn.execute(
            "SELECT COUNT(*) FROM followups WHERE updated_at > ?",
            ((now - timedelta(days=7)).timestamp(),),
        ).fetchone()
        result["followups_updated_last_7d"] = recent_updated[0] if recent_updated else 0

    return result


def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    now = datetime.now(timezone.utc)

    report = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "overall": "PASS",
        "runners": [],
    }

    has_fail = False
    has_warn = False

    for runner in RUNNERS:
        result = check_runner(conn, runner)
        report["runners"].append(result)
        if result["status"] == "FAIL":
            has_fail = True
        elif result["status"] == "WARN":
            has_warn = True

    conn.close()

    if has_fail:
        report["overall"] = "FAIL"
    elif has_warn:
        report["overall"] = "WARN"

    # Write report
    OPS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    # Append to watchdog alert if FAIL
    if has_fail:
        failing = [r for r in report["runners"] if r["status"] == "FAIL"]
        alert_lines = []
        for r in failing:
            alert_lines.append(f"RUNNER-HEALTH: {r['name']} FAIL — {'; '.join(r['issues'])}")
        alert_msg = "\n".join(alert_lines) + "\n"

        # Append to existing alert file (watchdog may have other alerts)
        with open(ALERT_FILE, "a") as f:
            f.write(alert_msg)

    # Print summary for cron log
    for r in report["runners"]:
        status = r["status"]
        issues = "; ".join(r["issues"]) if r["issues"] else "OK"
        print(f"[{status}] {r['name']}: {issues}")

    return 1 if has_fail else 0


if __name__ == "__main__":
    sys.exit(main())
