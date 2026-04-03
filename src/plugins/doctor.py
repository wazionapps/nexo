"""Doctor plugin — exposes nexo_doctor as MCP tool."""
from __future__ import annotations

import sys
import os
from pathlib import Path

# Ensure src/ is importable
SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def handle_doctor(tier: str = "boot", fix: bool = False, output: str = "text") -> str:
    """Unified diagnostic report for boot/runtime/deep health.

    Args:
        tier: Diagnostic tier — boot, runtime, deep, or all (default: boot)
        fix: Apply deterministic fixes (default: False)
        output: Output format — text or json (default: text)
    """
    from doctor.orchestrator import run_doctor
    from doctor.formatters import format_report

    if tier not in ("boot", "runtime", "deep", "all"):
        return f"Invalid tier '{tier}'. Use: boot, runtime, deep, all"
    if output not in ("text", "json"):
        return f"Invalid output '{output}'. Use: text, json"

    report = run_doctor(tier=tier, fix=fix)
    return format_report(report, fmt=output)


TOOLS = [
    (handle_doctor, "nexo_doctor", "Unified diagnostic report for boot/runtime/deep health."),
]
