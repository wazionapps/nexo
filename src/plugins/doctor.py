"""Doctor plugin — exposes nexo_doctor as MCP tool."""
from __future__ import annotations

import sys
import os
from pathlib import Path

# Ensure src/ is importable
SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def handle_doctor(tier: str = "boot", fix: bool = False, output: str = "text", plane: str = "") -> str:
    """Unified diagnostic report for boot/runtime/deep health.

    Args:
        tier: Diagnostic tier — boot, runtime, deep, or all (default: boot)
        fix: Apply deterministic fixes (default: False)
        output: Output format — text or json (default: text)
        plane: Diagnostic plane — runtime_personal, installation_live, or database_real
    """
    from doctor.orchestrator import run_doctor
    from doctor.formatters import format_report
    from doctor.planes import diagnostic_plane_choices

    if tier not in ("boot", "runtime", "deep", "all"):
        return f"Invalid tier '{tier}'. Use: boot, runtime, deep, all"
    if output not in ("text", "json"):
        return f"Invalid output '{output}'. Use: text, json"
    if not (plane or "").strip():
        valid_planes = diagnostic_plane_choices()
        if output == "json":
            return (
                "{"
                f"\"ok\": false, \"error\": \"Missing required argument: plane\", "
                "\"missing_argument\": \"plane\", "
                f"\"valid_planes\": {valid_planes!r}"
                "}"
            ).replace("'", '"')
        return (
            "Missing required argument: plane. "
            f"Use one of: {', '.join(valid_planes)}."
        )

    report = run_doctor(tier=tier, fix=fix, plane=plane)
    return format_report(report, fmt=output)


TOOLS = [
    (handle_doctor, "nexo_doctor", "Unified diagnostic report for boot/runtime/deep health."),
]
