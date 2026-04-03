"""Runtime tier checks — read-only health checks from existing artifacts. Target <5s."""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from doctor.models import DoctorCheck

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[2])))
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PROTECTED_MACOS_ROOTS = (
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path.home() / "Library" / "Mobile Documents",
)

# Freshness thresholds in seconds
IMMUNE_FRESHNESS = 3600  # 1 hour (runs every 30 min)
WATCHDOG_FRESHNESS = 3600  # 1 hour (runs every 30 min)
DEFAULT_CRON_THRESHOLD = 7200  # Fallback when manifest data is unavailable
SPECIAL_LAUNCHAGENT_IDS = {"prevent-sleep", "tcc-approve"}
SPECIAL_ENV_NORMALIZE_IDS = SPECIAL_LAUNCHAGENT_IDS
OPTIONALS_FILE = NEXO_HOME / "config" / "optionals.json"


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


def _enabled_optionals() -> dict[str, bool]:
    try:
        if OPTIONALS_FILE.is_file():
            data = json.loads(OPTIONALS_FILE.read_text())
            if isinstance(data, dict):
                return {str(k): bool(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def _enabled_manifest_crons() -> list[dict]:
    manifest_candidates = [
        NEXO_HOME / "crons" / "manifest.json",
        NEXO_CODE / "crons" / "manifest.json",
    ]
    optionals = _enabled_optionals()
    for manifest_path in manifest_candidates:
        if not manifest_path.is_file():
            continue
        try:
            data = _load_json(manifest_path)
        except Exception:
            continue

        enabled = []
        for cron in data.get("crons", []):
            cron_id = cron.get("id")
            if not cron_id:
                continue
            optional_key = cron.get("optional")
            if optional_key and not optionals.get(optional_key, False):
                continue
            enabled.append(cron)
        return enabled
    return []


def _cron_expectations() -> dict[str, dict]:
    expectations = {}
    for cron in _enabled_manifest_crons():
        cron_id = cron.get("id")
        if not cron_id or cron.get("run_at_load") or cron.get("keep_alive"):
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


def _run_at_load_cron_ids() -> set[str]:
    return {
        cron_id
        for cron_id, expected in _launchagent_schedule_expectations().items()
        if expected.get("RunAtLoad") is True
    }


def _launchagent_schedule_expectations() -> dict[str, dict]:
    expectations = {}
    for cron in _enabled_manifest_crons():
        cron_id = cron.get("id")
        if not cron_id:
            continue

        expected = {
            "StartInterval": None,
            "StartCalendarInterval": None,
            "RunAtLoad": None,
            "KeepAlive": None,
            "schedule_configured": False,
        }
        if cron.get("keep_alive"):
            expected["RunAtLoad"] = True
            expected["KeepAlive"] = True
            expected["schedule_configured"] = True
        elif cron.get("run_at_load"):
            expected["RunAtLoad"] = True
            expected["schedule_configured"] = True
        elif "interval_seconds" in cron:
            expected["StartInterval"] = int(cron["interval_seconds"])
            expected["schedule_configured"] = True
        elif "schedule" in cron:
            schedule = cron.get("schedule") or {}
            cal = {}
            if "hour" in schedule:
                cal["Hour"] = schedule["hour"]
            if "minute" in schedule:
                cal["Minute"] = schedule["minute"]
            if "weekday" in schedule:
                cal["Weekday"] = schedule["weekday"]
            expected["StartCalendarInterval"] = cal
            expected["schedule_configured"] = True
        expectations[cron_id] = expected
    return expectations


def _managed_launchagent_plists() -> list[tuple[str, Path]]:
    ids = set(SPECIAL_LAUNCHAGENT_IDS)
    for cron_id, expected in _launchagent_schedule_expectations().items():
        if expected.get("schedule_configured"):
            ids.add(cron_id)

    plists = []
    for cron_id in sorted(ids):
        plist_path = LAUNCH_AGENTS_DIR / f"com.nexo.{cron_id}.plist"
        if plist_path.is_file():
            plists.append((cron_id, plist_path))
    return plists


def _extract_launchctl_value(output: str, prefix: str) -> str | None:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
    return None


def _is_protected_macos_path(value: str | os.PathLike[str] | None) -> bool:
    if not value or platform.system() != "Darwin":
        return False
    try:
        raw = str(value).replace("~", str(Path.home()), 1)
        candidate = Path(raw).expanduser().resolve(strict=False)
    except Exception:
        return False
    return any(candidate == root or root in candidate.parents for root in PROTECTED_MACOS_ROOTS)


def _plist_runtime_paths(plist_data: dict) -> list[str]:
    paths: list[str] = []
    env = plist_data.get("EnvironmentVariables") or {}
    for key in ("NEXO_HOME", "NEXO_CODE"):
        value = env.get(key)
        if value:
            paths.append(str(value))
    for arg in plist_data.get("ProgramArguments") or []:
        arg_str = str(arg)
        if arg_str.startswith("/") or arg_str.startswith("~"):
            paths.append(arg_str)
    return paths


def _recent_permission_denial(cron_id: str, max_age_seconds: int = 7 * 86400) -> bool:
    stderr_path = NEXO_HOME / "logs" / f"{cron_id}-stderr.log"
    age = _file_age_seconds(stderr_path)
    if age is None or age > max_age_seconds:
        return False
    try:
        tail = "\n".join(stderr_path.read_text(errors="ignore").splitlines()[-50:])
    except Exception:
        return False
    return "Operation not permitted" in tail


def _repair_launchagents(items: list[tuple[str, Path]]) -> tuple[bool, list[str]]:
    evidence = []
    uid = str(os.getuid())
    ok = True
    for cron_id, plist_path in items:
        label = f"com.nexo.{cron_id}"
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/{label}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        result = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            ok = False
            evidence.append(f"{label}: {result.stderr.strip() or result.stdout.strip() or 'bootstrap failed'}")
    return ok, evidence


def _repair_special_launchagent_plists(items: list[tuple[str, Path]]) -> tuple[bool, list[str]]:
    evidence: list[str] = []
    ok = True
    for cron_id, plist_path in items:
        if cron_id not in SPECIAL_ENV_NORMALIZE_IDS:
            continue
        try:
            with plist_path.open("rb") as fh:
                plist_data = plistlib.load(fh)
            env = plist_data.setdefault("EnvironmentVariables", {})
            changed = False
            if env.get("NEXO_CODE") != str(NEXO_HOME):
                env["NEXO_CODE"] = str(NEXO_HOME)
                changed = True
            if env.get("NEXO_HOME") != str(NEXO_HOME):
                env["NEXO_HOME"] = str(NEXO_HOME)
                changed = True
            if changed:
                with plist_path.open("wb") as fh:
                    plistlib.dump(plist_data, fh)
                evidence.append(f"com.nexo.{cron_id}: normalized special LaunchAgent env")
        except Exception as e:
            ok = False
            evidence.append(f"com.nexo.{cron_id}: {e}")
    return ok, evidence


def _sync_launchagents_from_manifest() -> tuple[bool, list[str]]:
    sync_path = NEXO_CODE / "crons" / "sync.py"
    if not sync_path.is_file():
        return False, [f"cron sync script not found at {sync_path}"]

    try:
        result = subprocess.run(
            [sys.executable, str(sync_path)],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "NEXO_HOME": str(NEXO_HOME), "NEXO_CODE": str(NEXO_CODE)},
        )
    except Exception as e:
        return False, [f"cron sync failed: {e}"]

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "cron sync failed"
        return False, [detail]
    return True, []


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
        ignored_crons = _run_at_load_cron_ids()
        tracked_crons = set(expectations)
        now = time.time()
        for row in rows:
            cron_id = row[0]
            if cron_id in ignored_crons:
                continue
            if cron_id not in tracked_crons:
                continue
            parsed = _parse_timestamp(row[1]) if row[1] else None
            if parsed is None:
                stale.append(f"{cron_id}: unreadable timestamp {row[1]!r}")
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)

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
            summary=f"All {len(tracked_crons)} tracked crons ran recently",
        )
    except Exception as e:
        return DoctorCheck(
            id="runtime.cron_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Cron check failed: {e}",
        )


def check_launchagent_integrity(fix: bool = False) -> DoctorCheck:
    """Check that core LaunchAgents are loaded from the real plist paths, not temp installs."""
    if platform.system() != "Darwin":
        return DoctorCheck(
            id="runtime.launchagents",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="LaunchAgent integrity check skipped on non-macOS",
        )

    managed = _managed_launchagent_plists()
    if not managed:
        return DoctorCheck(
            id="runtime.launchagents",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="No managed LaunchAgents found on disk",
        )

    uid = str(os.getuid())
    problems = []
    problem_items: list[tuple[str, Path]] = []
    tmp_drift = False
    tcc_risk = False
    tcc_failure = False
    schedule_expectations = _launchagent_schedule_expectations()
    for cron_id, plist_path in managed:
        label = f"com.nexo.{cron_id}"
        had_problem = False
        try:
            result = subprocess.run(
                ["launchctl", "print", f"gui/{uid}/{label}"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except Exception as e:
            problems.append(f"{label}: launchctl print failed ({e})")
            continue

        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 or "Could not find service" in output:
            problems.append(f"{label}: not loaded")
            had_problem = True
            problem_items.append((cron_id, plist_path))
            continue

        expected_path = str(plist_path)
        actual_path = _extract_launchctl_value(output, "path = ")
        if actual_path != expected_path:
            problems.append(f"{label}: loaded from {actual_path or 'unknown path'}")
            had_problem = True
            if actual_path and "/tmp/" in actual_path:
                tmp_drift = True

        try:
            with plist_path.open("rb") as fh:
                plist_data = plistlib.load(fh)
            env = plist_data.get("EnvironmentVariables") or {}
        except Exception as e:
            problems.append(f"{label}: plist unreadable ({e})")
            continue

        protected_refs = [path for path in _plist_runtime_paths(plist_data) if _is_protected_macos_path(path)]
        if protected_refs:
            tcc_risk = True
            if _recent_permission_denial(cron_id):
                tcc_failure = True
                problems.append(
                    f"{label}: recent 'Operation not permitted' while using protected macOS path {protected_refs[0]}"
                )
            else:
                problems.append(f"{label}: runtime points into protected macOS path {protected_refs[0]}")
            had_problem = True

        for env_key in ("NEXO_HOME", "NEXO_CODE"):
            expected_value = env.get(env_key)
            if not expected_value:
                continue
            marker = f"{env_key} => {expected_value}"
            if marker not in output:
                problems.append(f"{label}: {env_key} drift")
                had_problem = True
                if "/tmp/" in output:
                    tmp_drift = True

        expected_schedule = schedule_expectations.get(cron_id)
        if expected_schedule is not None and expected_schedule.get("schedule_configured"):
            actual_schedule = {
                "StartInterval": plist_data.get("StartInterval"),
                "StartCalendarInterval": plist_data.get("StartCalendarInterval"),
                "RunAtLoad": plist_data.get("RunAtLoad"),
                "KeepAlive": plist_data.get("KeepAlive"),
            }
            target_schedule = {
                "StartInterval": expected_schedule.get("StartInterval"),
                "StartCalendarInterval": expected_schedule.get("StartCalendarInterval"),
                "RunAtLoad": expected_schedule.get("RunAtLoad"),
                "KeepAlive": expected_schedule.get("KeepAlive"),
            }
            if actual_schedule != target_schedule:
                problems.append(
                    f"{label}: schedule drift "
                    f"(actual={actual_schedule}, expected={target_schedule})"
                )
                had_problem = True

        if had_problem:
            problem_items.append((cron_id, plist_path))

    if not problems:
        return DoctorCheck(
            id="runtime.launchagents",
            tier="runtime",
            status="healthy",
            severity="info",
            summary=f"LaunchAgents aligned for {len(managed)} managed job(s)",
        )

    check = DoctorCheck(
        id="runtime.launchagents",
        tier="runtime",
        status="critical" if (tmp_drift or tcc_failure) else "degraded",
        severity="error" if (tmp_drift or tcc_failure) else "warn",
        summary=(
            f"LaunchAgent drift detected in {len(problems)} job(s)"
            if not tcc_risk
            else f"LaunchAgent drift or TCC/runtime path risk detected in {len(problems)} job(s)"
        ),
        evidence=problems[:10],
        repair_plan=[
            "Reload the affected LaunchAgents from ~/Library/LaunchAgents",
            "Re-sync core cron plists from crons/manifest.json if the schedule drifted",
            "If any job is loaded from /tmp, boot it out before bootstrapping the real plist",
            "If any core job points into Documents/Desktop/Downloads, re-sync it so it runs from NEXO_HOME instead",
        ],
        escalation_prompt=(
            "Launchd is serving stale or drifted NEXO jobs. Compare loaded job paths with plist paths on disk, "
            "and treat recent 'Operation not permitted' against Documents/Desktop/Downloads as a TCC/runtime path issue."
        ),
    )

    if fix:
        sync_ok, sync_evidence = _sync_launchagents_from_manifest()
        special_ok, special_evidence = _repair_special_launchagent_plists(problem_items)
        repaired, repair_evidence = _repair_launchagents(problem_items)
        if sync_ok and special_ok and repaired:
            post_check = check_launchagent_integrity(fix=False)
            if post_check.status == "healthy":
                post_check.fixed = True
                post_check.summary += " (fixed)"
                return post_check
        check.evidence.extend((sync_evidence + special_evidence + repair_evidence)[:10])
    return check


def check_skill_health(fix: bool = False) -> DoctorCheck:
    """Check executable skill consistency and approval state."""
    try:
        from db import get_skill_health_report
        report = get_skill_health_report(fix=fix)
    except Exception as e:
        return DoctorCheck(
            id="runtime.skills",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Skill health check failed: {e}",
        )

    issues = report.get("issues", [])
    if not issues:
        summary = f"Skills consistent ({report.get('checked', 0)} checked)"
        if fix:
            summary += " (fixed)"
        return DoctorCheck(
            id="runtime.skills",
            tier="runtime",
            status="healthy",
            severity="info",
            summary=summary,
            fixed=fix,
        )

    errors = [issue for issue in issues if issue.get("severity") == "error"]
    warnings = [issue for issue in issues if issue.get("severity") != "error"]
    status = "critical" if errors else "degraded"
    severity = "error" if errors else "warn"
    evidence = [f"{issue['skill_id']}: {issue['message']}" for issue in issues[:10]]
    return DoctorCheck(
        id="runtime.skills",
        tier="runtime",
        status=status,
        severity=severity,
        summary=f"Skill issues detected in {len(issues)} item(s)",
        evidence=evidence,
        repair_plan=[
            "Run nexo skills sync to reconcile filesystem definitions",
            "Auto-reconcile execution metadata for executable skills",
            "Fix or restore missing executable files for execute/hybrid skills",
        ],
        escalation_prompt="Skill metadata and filesystem artifacts are out of sync or an executable skill is missing artifacts.",
    )


def run_runtime_checks(fix: bool = False) -> list[DoctorCheck]:
    """Run all runtime-tier checks. Read-only by default."""
    return [
        check_immune_status(),
        check_watchdog_status(),
        check_stale_sessions(),
        check_cron_freshness(),
        check_launchagent_integrity(fix=fix),
        check_skill_health(fix=fix),
    ]
