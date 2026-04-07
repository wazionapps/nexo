"""Doctor data models — check results and report structure."""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class DoctorCheck:
    id: str
    tier: str
    status: str  # healthy, degraded, critical
    severity: str  # info, warn, error
    summary: str
    evidence: list[str] = field(default_factory=list)
    repair_plan: list[str] = field(default_factory=list)
    escalation_prompt: str = ""
    fixed: bool = False


@dataclass
class DoctorReport:
    overall_status: str  # healthy, degraded, critical
    counts: dict = field(default_factory=dict)
    checks: list[DoctorCheck] = field(default_factory=list)
    duration_ms: int = 0

    def add(self, check: DoctorCheck):
        self.checks.append(check)

    def compute_status(self):
        """Compute overall status from individual checks."""
        statuses = [c.status for c in self.checks]
        if "critical" in statuses:
            self.overall_status = "critical"
        elif "degraded" in statuses:
            self.overall_status = "degraded"
        else:
            self.overall_status = "healthy"
        self.counts = {
            "healthy": statuses.count("healthy"),
            "degraded": statuses.count("degraded"),
            "critical": statuses.count("critical"),
            "total": len(statuses),
        }


def safe_check(fn: Callable[..., DoctorCheck], *args, **kwargs) -> DoctorCheck:
    """Run a single check function, returning a crash DoctorCheck on exception.

    This isolates individual checks so one failure doesn't take down
    all sibling checks within a tier.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        last_frame = tb[-1].strip() if tb else str(exc)
        check_name = getattr(fn, "__name__", "unknown")
        return DoctorCheck(
            id=f"check.{check_name}_crashed",
            tier="unknown",
            status="critical",
            severity="error",
            summary=f"Check {check_name} crashed: {type(exc).__name__}: {exc}",
            evidence=[last_frame],
            repair_plan=[f"Investigate {check_name} — exception during check execution"],
        )
