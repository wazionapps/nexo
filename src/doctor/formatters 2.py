"""Doctor output formatters — text and JSON."""
from __future__ import annotations

import json
from dataclasses import asdict

from doctor.models import DoctorReport


def format_report(report: DoctorReport, fmt: str = "text") -> str:
    """Format a DoctorReport as text or JSON."""
    if fmt == "json":
        return json.dumps(asdict(report), indent=2, ensure_ascii=False)
    return _format_text(report)


def _format_text(report: DoctorReport) -> str:
    """Human-friendly text output."""
    lines = []

    # Header
    icon = {"healthy": "✓", "degraded": "⚠", "critical": "✗"}.get(report.overall_status, "?")
    lines.append(f"NEXO Doctor — {icon} {report.overall_status.upper()}")
    lines.append(f"  {report.counts.get('healthy', 0)} healthy, "
                 f"{report.counts.get('degraded', 0)} degraded, "
                 f"{report.counts.get('critical', 0)} critical "
                 f"({report.duration_ms}ms)")
    lines.append("")

    # Group by tier
    current_tier = None
    for check in report.checks:
        if check.tier != current_tier:
            current_tier = check.tier
            lines.append(f"── {current_tier.upper()} ──")

        icon = {"healthy": "✓", "degraded": "⚠", "critical": "✗"}.get(check.status, "?")
        fixed = " [FIXED]" if check.fixed else ""
        lines.append(f"  {icon} {check.summary}{fixed}")

        if check.status != "healthy":
            for ev in check.evidence:
                lines.append(f"    → {ev}")
            if check.repair_plan:
                lines.append("    Fix:")
                for step in check.repair_plan:
                    lines.append(f"      • {step}")
            if check.escalation_prompt:
                lines.append(f"    Escalation: {check.escalation_prompt}")

    lines.append("")
    return "\n".join(lines)
