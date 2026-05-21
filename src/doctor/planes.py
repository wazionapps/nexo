"""Diagnostic plane preflight for NEXO Doctor."""

from __future__ import annotations

from doctor.models import DoctorCheck

VALID_DIAGNOSTIC_PLANES = {
    "product_public": {
        "label": "public product",
        "use": "release contracts, published artifacts, compare/, docs, and public repo surfaces",
    },
    "runtime_personal": {
        "label": "personal runtime",
        "use": "~/.nexo, personal scripts, followups, reminders, and operator work habits",
    },
    "installation_live": {
        "label": "live installation",
        "use": "installed runtime, active hooks, connected clients, cron sync, and local installation parity",
    },
    "database_real": {
        "label": "real database",
        "use": "real SQLite/MySQL, rows, schema, debts, sessions, and persisted evidence",
    },
    "cooperator": {
        "label": "co-operator",
        "use": "agent behavior, protocol, communication, and assistant decisions",
    },
}

DOCTOR_COMPATIBLE_PLANES = {"runtime_personal", "installation_live", "database_real"}
DEFAULT_DOCTOR_PLANE = "runtime_personal"


def normalize_diagnostic_plane(plane: str = "") -> str:
    clean = (plane or "").strip().lower().replace("-", "_").replace(" ", "_")
    return clean if clean in VALID_DIAGNOSTIC_PLANES else ""


def diagnostic_plane_choices() -> list[str]:
    return sorted(VALID_DIAGNOSTIC_PLANES)


def diagnostic_plane_preflight(plane: str = "") -> tuple[str, DoctorCheck | None]:
    raw_plane = str(plane or "").strip()
    if not raw_plane:
        return DEFAULT_DOCTOR_PLANE, None

    clean_plane = normalize_diagnostic_plane(raw_plane)
    if not clean_plane:
        options = ", ".join(diagnostic_plane_choices())
        return "", DoctorCheck(
            id="orchestrator.diagnostic_plane_invalid",
            tier="orchestrator",
            status="critical",
            severity="error",
            summary=f"Unknown diagnostic plane: {raw_plane}",
            evidence=[
                f"valid planes: {options}",
                "Use `runtime_personal` for ~/.nexo and runtime habits; `installation_live` for hooks/clients/installation; `database_real` for real rows and schema.",
            ],
            repair_plan=[
                "Run `nexo_doctor` or `nexo doctor` again with `plane='runtime_personal'`, `plane='installation_live'`, or `plane='database_real'`.",
                "If the issue belongs to the public product or the co-operator, use the correct surface instead of NEXO Doctor.",
            ],
            escalation_prompt=(
                "The selected plane does not exist. Repeat the diagnosis with a valid plane to avoid mixing runtime, installation, real DB, or non-doctor surfaces."
            ),
        )

    if clean_plane not in DOCTOR_COMPATIBLE_PLANES:
        plane_info = VALID_DIAGNOSTIC_PLANES[clean_plane]
        return clean_plane, DoctorCheck(
            id="orchestrator.diagnostic_plane_mismatch",
            tier="orchestrator",
            status="degraded",
            severity="warn",
            summary=f"NEXO Doctor is not the correct surface for the {plane_info['label']} plane",
            evidence=[
                f"plane: {clean_plane}",
                f"this plane is better diagnosed from: {plane_info['use']}",
            ],
            repair_plan=[
                "If you want to diagnose runtime/installation/DB, rerun the doctor with the correct plane.",
                "If the issue belongs to the public product or the co-operator, use release checks, repo checks, or protocol/session tools instead of Doctor.",
            ],
            escalation_prompt=(
                "The selected plane does not belong to the runtime doctor. Change the plane or tool before continuing so technical diagnosis is not mixed with product or agent behavior."
            ),
        )

    return clean_plane, None
