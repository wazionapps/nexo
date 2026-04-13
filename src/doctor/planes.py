"""Diagnostic plane preflight for NEXO Doctor."""

from __future__ import annotations

from doctor.models import DoctorCheck

VALID_DIAGNOSTIC_PLANES = {
    "product_public": {
        "label": "producto público",
        "use": "release contracts, artefactos publicados, compare/, docs y surfaces públicas del repo",
    },
    "runtime_personal": {
        "label": "runtime personal",
        "use": "~/.nexo, scripts personales, followups, reminders y hábitos operativos del operador",
    },
    "installation_live": {
        "label": "instalación viva",
        "use": "runtime instalado, hooks activos, clientes conectados, cron sync y parity de la instalación local",
    },
    "database_real": {
        "label": "BD real",
        "use": "SQLite/MySQL reales, filas, schema, deudas, sesiones y evidencia persistida",
    },
    "cooperator": {
        "label": "co-operador",
        "use": "comportamiento del agente, protocolo, comunicación y decisiones del asistente",
    },
}

DOCTOR_COMPATIBLE_PLANES = {"runtime_personal", "installation_live", "database_real"}


def normalize_diagnostic_plane(plane: str = "") -> str:
    clean = (plane or "").strip().lower().replace("-", "_").replace(" ", "_")
    return clean if clean in VALID_DIAGNOSTIC_PLANES else ""


def diagnostic_plane_choices() -> list[str]:
    return sorted(VALID_DIAGNOSTIC_PLANES)


def diagnostic_plane_preflight(plane: str = "") -> tuple[str, DoctorCheck | None]:
    clean_plane = normalize_diagnostic_plane(plane)
    if not clean_plane:
        options = ", ".join(diagnostic_plane_choices())
        return "", DoctorCheck(
            id="orchestrator.diagnostic_plane_required",
            tier="orchestrator",
            status="critical",
            severity="error",
            summary="El diagnóstico está bloqueado hasta fijar explícitamente el plano",
            evidence=[
                f"planes válidos: {options}",
                "Usa `runtime_personal` para ~/.nexo y hábitos del runtime; `installation_live` para hooks/clientes/instalación; `database_real` para filas y schema reales.",
            ],
            repair_plan=[
                "Repite `nexo_doctor` o `nexo doctor` con `plane='runtime_personal'`, `plane='installation_live'` o `plane='database_real'`.",
                "Si el problema pertenece a producto público o al co-operador, usa el surface correcto en vez de NEXO Doctor.",
            ],
            escalation_prompt=(
                "NEXO mezcló planos en diagnósticos anteriores. El doctor no debe correr hasta que se elija "
                "explícitamente si el problema está en producto público, runtime personal, instalación viva, BD real o co-operador."
            ),
        )

    if clean_plane not in DOCTOR_COMPATIBLE_PLANES:
        plane_info = VALID_DIAGNOSTIC_PLANES[clean_plane]
        return clean_plane, DoctorCheck(
            id="orchestrator.diagnostic_plane_mismatch",
            tier="orchestrator",
            status="degraded",
            severity="warn",
            summary=f"NEXO Doctor no es la superficie correcta para el plano {plane_info['label']}",
            evidence=[
                f"plane: {clean_plane}",
                f"este plano se diagnostica mejor desde: {plane_info['use']}",
            ],
            repair_plan=[
                "Si quieres diagnosticar runtime/instalación/BD, vuelve a lanzar el doctor con el plano correcto.",
                "Si el problema es del producto público o del co-operador, usa release checks, repo checks o herramientas de protocolo/sesión en vez de Doctor.",
            ],
            escalation_prompt=(
                "El plano elegido no corresponde al runtime doctor. Cambia de plano o de herramienta antes de seguir para no mezclar diagnóstico técnico con producto o comportamiento del agente."
            ),
        )

    return clean_plane, None
