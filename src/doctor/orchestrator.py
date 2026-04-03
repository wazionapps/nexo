"""Doctor orchestrator — runs providers by tier, aggregates results."""
from __future__ import annotations

import time

from doctor.models import DoctorReport
from doctor.providers.boot import run_boot_checks
from doctor.providers.runtime import run_runtime_checks
from doctor.providers.deep import run_deep_checks


_TIER_RUNNERS = {
    "boot": run_boot_checks,
    "runtime": run_runtime_checks,
    "deep": run_deep_checks,
}

_TIER_ORDER = ["boot", "runtime", "deep"]


def run_doctor(tier: str = "boot", fix: bool = False) -> DoctorReport:
    """Run diagnostic checks for the specified tier(s).

    Args:
        tier: "boot", "runtime", "deep", or "all"
        fix: If True, apply deterministic fixes where possible
    """
    report = DoctorReport(overall_status="healthy")
    start = time.monotonic()

    tiers = _TIER_ORDER if tier == "all" else [tier]

    for t in tiers:
        runner = _TIER_RUNNERS.get(t)
        if runner:
            checks = runner(fix=fix)
            for check in checks:
                report.add(check)

    report.compute_status()
    report.duration_ms = int((time.monotonic() - start) * 1000)
    return report
