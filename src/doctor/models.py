"""Doctor data models — check results and report structure."""
from __future__ import annotations

from dataclasses import dataclass, field


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
