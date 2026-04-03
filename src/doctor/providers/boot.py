"""Boot tier checks — fast, native, no repair. Target <100ms."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from doctor.models import DoctorCheck

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


def check_db_exists() -> DoctorCheck:
    """Check that the main database file exists and is readable."""
    db_path = NEXO_HOME / "data" / "nexo.db"
    if db_path.is_file():
        size_kb = db_path.stat().st_size / 1024
        return DoctorCheck(
            id="boot.db_exists",
            tier="boot",
            status="healthy",
            severity="info",
            summary=f"Database exists ({size_kb:.0f} KB)",
            evidence=[str(db_path)],
        )
    return DoctorCheck(
        id="boot.db_exists",
        tier="boot",
        status="critical",
        severity="error",
        summary="Database file not found",
        evidence=[f"Expected: {db_path}"],
        repair_plan=["Run nexo-brain to initialize the database"],
        escalation_prompt="NEXO database missing — server cannot start without it.",
    )


def check_required_dirs() -> DoctorCheck:
    """Check that required NEXO_HOME directories exist."""
    required = ["data", "scripts", "plugins", "crons", "hooks", "coordination", "operations", "logs"]
    missing = [d for d in required if not (NEXO_HOME / d).is_dir()]

    if not missing:
        return DoctorCheck(
            id="boot.required_dirs",
            tier="boot",
            status="healthy",
            severity="info",
            summary=f"All {len(required)} required directories present",
        )

    return DoctorCheck(
        id="boot.required_dirs",
        tier="boot",
        status="degraded" if len(missing) < 3 else "critical",
        severity="warn" if len(missing) < 3 else "error",
        summary=f"{len(missing)} required director{'y' if len(missing) == 1 else 'ies'} missing",
        evidence=[f"Missing: {d}" for d in missing],
        repair_plan=[f"mkdir -p {NEXO_HOME / d}" for d in missing],
    )


def check_disk_space() -> DoctorCheck:
    """Check disk free space on NEXO_HOME partition."""
    try:
        usage = shutil.disk_usage(str(NEXO_HOME))
        free_gb = usage.free / (1024 ** 3)
        pct_free = (usage.free / usage.total) * 100

        if free_gb < 1:
            return DoctorCheck(
                id="boot.disk_space",
                tier="boot",
                status="critical",
                severity="error",
                summary=f"Very low disk space: {free_gb:.1f} GB free ({pct_free:.0f}%)",
                evidence=[f"Total: {usage.total / (1024**3):.0f} GB, Free: {free_gb:.1f} GB"],
                repair_plan=["Free up disk space — NEXO needs at least 1 GB for normal operation"],
                escalation_prompt="Disk space critically low — backups and logs may fail.",
            )
        elif free_gb < 5:
            return DoctorCheck(
                id="boot.disk_space",
                tier="boot",
                status="degraded",
                severity="warn",
                summary=f"Low disk space: {free_gb:.1f} GB free ({pct_free:.0f}%)",
                evidence=[f"Total: {usage.total / (1024**3):.0f} GB, Free: {free_gb:.1f} GB"],
            )
        return DoctorCheck(
            id="boot.disk_space",
            tier="boot",
            status="healthy",
            severity="info",
            summary=f"Disk space OK: {free_gb:.0f} GB free ({pct_free:.0f}%)",
        )
    except Exception as e:
        return DoctorCheck(
            id="boot.disk_space",
            tier="boot",
            status="degraded",
            severity="warn",
            summary=f"Could not check disk space: {e}",
        )


def check_wrapper_scripts() -> DoctorCheck:
    """Check that cron wrapper script exists."""
    wrapper = NEXO_HOME / "scripts" / "nexo-cron-wrapper.sh"
    if wrapper.is_file():
        return DoctorCheck(
            id="boot.wrapper_scripts",
            tier="boot",
            status="healthy",
            severity="info",
            summary="Cron wrapper script present",
        )
    return DoctorCheck(
        id="boot.wrapper_scripts",
        tier="boot",
        status="degraded",
        severity="warn",
        summary="Cron wrapper script missing",
        evidence=[f"Expected: {wrapper}"],
        repair_plan=["Run nexo-brain to reinstall wrapper scripts"],
    )


def check_python_runtime() -> DoctorCheck:
    """Check Python interpreter is suitable."""
    version = sys.version_info
    if version >= (3, 10):
        return DoctorCheck(
            id="boot.python_runtime",
            tier="boot",
            status="healthy",
            severity="info",
            summary=f"Python {version.major}.{version.minor}.{version.micro}",
        )
    return DoctorCheck(
        id="boot.python_runtime",
        tier="boot",
        status="degraded",
        severity="warn",
        summary=f"Python {version.major}.{version.minor} — 3.10+ recommended",
        evidence=[sys.executable],
    )


def check_config_parse() -> DoctorCheck:
    """Check schedule.json parses correctly if present."""
    schedule_file = NEXO_HOME / "config" / "schedule.json"
    if not schedule_file.exists():
        return DoctorCheck(
            id="boot.config_parse",
            tier="boot",
            status="healthy",
            severity="info",
            summary="No schedule.json (using defaults)",
        )
    try:
        import json
        json.loads(schedule_file.read_text())
        return DoctorCheck(
            id="boot.config_parse",
            tier="boot",
            status="healthy",
            severity="info",
            summary="schedule.json parses OK",
        )
    except Exception as e:
        return DoctorCheck(
            id="boot.config_parse",
            tier="boot",
            status="degraded",
            severity="warn",
            summary=f"schedule.json parse error: {e}",
            repair_plan=["Fix JSON syntax in schedule.json or delete to use defaults"],
        )


def run_boot_checks(fix: bool = False) -> list[DoctorCheck]:
    """Run all boot-tier checks."""
    checks = [
        check_db_exists(),
        check_required_dirs(),
        check_disk_space(),
        check_wrapper_scripts(),
        check_python_runtime(),
        check_config_parse(),
    ]

    if fix:
        for check in checks:
            if check.id == "boot.required_dirs" and check.status != "healthy":
                # Deterministic fix: create missing directories
                for plan in check.repair_plan:
                    if plan.startswith("mkdir"):
                        dir_path = plan.split("mkdir -p ")[-1]
                        Path(dir_path).mkdir(parents=True, exist_ok=True)
                check.fixed = True
                check.status = "healthy"
                check.summary += " (fixed)"

    return checks
