"""Doctor orchestrator — runs providers by tier, aggregates results."""
from __future__ import annotations

import sys
import time
import traceback

from doctor.models import DoctorCheck, DoctorReport
from doctor.providers.boot import run_boot_checks
from doctor.providers.runtime import run_runtime_checks
from doctor.providers.deep import run_deep_checks


_TIER_RUNNERS = {
    "boot": run_boot_checks,
    "runtime": run_runtime_checks,
    "deep": run_deep_checks,
}

_TIER_ORDER = ["boot", "runtime", "deep"]

VALID_TIERS = frozenset(_TIER_ORDER) | {"all"}


def run_doctor(tier: str = "boot", fix: bool = False) -> DoctorReport:
    """Run diagnostic checks for the specified tier(s).

    Args:
        tier: "boot", "runtime", "deep", or "all"
        fix: If True, apply deterministic fixes where possible
    """
    report = DoctorReport(overall_status="healthy")
    start = time.monotonic()

    if tier not in VALID_TIERS:
        report.add(DoctorCheck(
            id="orchestrator.invalid_tier",
            tier="orchestrator",
            status="critical",
            severity="error",
            summary=f"Unknown tier '{tier}' — valid options: {', '.join(sorted(VALID_TIERS))}",
        ))
        report.compute_status()
        report.duration_ms = int((time.monotonic() - start) * 1000)
        return report

    tiers = _TIER_ORDER if tier == "all" else [tier]

    for t in tiers:
        runner = _TIER_RUNNERS.get(t)
        if not runner:
            continue
        try:
            checks = runner(fix=fix)
            for check in checks:
                report.add(check)
        except Exception as exc:
            tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
            last_frame = tb[-1].strip() if tb else str(exc)
            report.add(DoctorCheck(
                id=f"orchestrator.{t}_crashed",
                tier=t,
                status="critical",
                severity="error",
                summary=f"{t} tier checks crashed: {type(exc).__name__}: {exc}",
                evidence=[last_frame],
                repair_plan=[f"Investigate {t} provider — exception during check execution"],
            ))

    report.compute_status()
    report.duration_ms = int((time.monotonic() - start) * 1000)
    return report
