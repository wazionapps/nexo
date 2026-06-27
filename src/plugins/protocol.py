"""Protocol discipline plugin — persistent task contracts for NEXO."""

from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
import time
from pathlib import Path

import paths
from db import (
    VALID_TASK_TYPES,
    VALID_CLOSE_OUTCOMES,
    close_protocol_task,
    create_followup,
    latest_cortex_evaluation_for_task,
    create_protocol_debt,
    create_protocol_task,
    build_pre_action_context,
    capture_context_event,
    format_pre_action_context_bundle,
    get_db,
    get_followup,
    get_followups,
    get_reminder,
    get_protocol_task,
    list_session_correction_requirements,
    set_protocol_task_guard_acknowledged,
    list_workflow_goals,
    list_workflow_runs,
    list_protocol_debts,
    log_change,
    resolve_protocol_debts,
    search_learnings,
    task_has_cortex_evaluation,
    validate_close_outcome,
    validate_task_type,
)
from plugins.cortex import evaluate_cortex_state
from plugins.guard import handle_guard_check
from protocol_settings import get_protocol_strictness
from tools_sessions import handle_heartbeat
from user_state_model import OPERATIONAL_SOURCE_PREFIXES, build_operational_state_policy

try:
    from tools_hot_context import append_local_context_evidence
except Exception:  # pragma: no cover - local context is optional in early boot
    append_local_context_evidence = None


# ── R03 (Fase 2 Protocol Enforcer) evidence quality thresholds ────────
# "evidence" supplied to nexo_task_close must be substantive when an
# outcome of "done" is claimed. The plan doc 1 R03 rule reads:
#   SI nexo_task_close Y evidence <50 chars / solo "done|listo|fixed"
#   ENTONCES rechazar hasta recibir evidence real.
# Fase 2 spec 0.19 does not promote R03 to CORE (operators can downgrade
# to soft in guardian.json), but strict mode rejects trivial evidence.
R03_MIN_EVIDENCE_CHARS = 50
R03_TRIVIAL_EVIDENCE_PATTERN = re.compile(
    r"^(done|listo|hecho|fixed|ok|ready|finished|completado|complete|"
    r"terminado|arreglado|cerrado|solved|resuelto)\s*[\.!]*\s*$",
    re.IGNORECASE,
)
P0_P1_FINDING_PATTERN = re.compile(
    r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)?(?:\*\*)?"
    r"(P[01])(?:\*\*)?\s*(?:[:\-–—\])\)]|\b)",
    re.IGNORECASE,
)
FOLLOWUP_REF_PATTERN = re.compile(r"\bNF-[A-Z0-9][A-Z0-9-]*\b", re.IGNORECASE)
ANALYZE_ARTIFACT_SUFFIXES = {".md", ".markdown", ".txt"}
MEDIUM_IMPACT_EDIT_EXTENSIONS = (
    ".php",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".py",
    ".sh",
    ".yml",
    ".yaml",
)
MEDIUM_IMPACT_EDIT_PATH_MARKERS = (
    "/Documents/_PhpstormProjects/",
    "/public_html/",
    "/httpdocs/",
    "/var/www/",
    "/home/nexodesk/",
    "/opt/",
    "/scripts/",
    "/infra/",
    "/cron/",
    "/src/",
)
LEARNING_WORTHY_EDIT_RE = re.compile(
    r"\b("
    r"fix|fixed|resolved|solution|reusable|pattern|"
    r"correg(?:ir|ido|ida|i|ido)?|arregl(?:ar|ado|ada|e)|fallo|"
    r"soluci[oó]n|reutilizable|patr[oó]n"
    r")\b",
    re.IGNORECASE,
)
LEARNING_TRACE_RE = re.compile(
    r"\b(nexo_learning_add|learning_id|learning\s*#|aprendizaje\s*#)\b",
    re.IGNORECASE,
)
REPEATED_SYMPTOM_P0_RE = re.compile(
    r"\b("
    r"mismo\s+s[ií]ntoma|same\s+symptom|s[ií]ntoma\s+reportad[oa]|"
    r"2\+\s*veces|dos\s+veces|3\+\s*veces|tres\s+veces|"
    r"repetid[oa]|recurrente|reaparec|otra\s+vez|"
    r"te\s+he\s+avisado|10000\s+veces"
    r")\b",
    re.IGNORECASE,
)
P0_REPRO_TEST_RE = re.compile(
    r"(?=.*\b(?:test|pytest|vitest|phpunit|playwright)\b)"
    r"(?=.*\b(?:reproductor|repro|regresi[oó]n)\b)"
    r"(?=.*\b(?:rojo\s*[-→>]+\s*verde|red\s*[-→>]+\s*green|"
    r"falla\s+pre[-\s]?fix|failed\s+pre[-\s]?fix|"
    r"pasa\s+post[-\s]?fix|passed\s+post[-\s]?fix)\b)",
    re.IGNORECASE | re.DOTALL,
)
P0_BACKEND_FLOW_RE = re.compile(
    r"(?=.*\b(?:backend|api|flow|flujo|endpoint)\b)"
    r"(?=.*\b(?:curl|sql|select\b|mysql|psql|cloud\s+sql|bd\s+producci[oó]n|production\s+db|http\s*200)\b)",
    re.IGNORECASE | re.DOTALL,
)
P0_UI_EVIDENCE_RE = re.compile(
    r"(?=.*\b(?:ui|front[- ]?end|frontend|browser|navegador|pantalla|modal|web|renderer)\b)"
    r"(?=.*\b(?:screenshot|captura|log|playwright|headed|video)\b)"
    r"(?=.*\b(?:post[-\s]?fix|despu[eé]s\s+del\s+fix|fixed|corregid[oa])\b)",
    re.IGNORECASE | re.DOTALL,
)
DESKTOP_RELEASE_RE = re.compile(
    r"(?=.*\b(?:nexo\s+desktop|desktop)\b)(?=.*\brelease\b)",
    re.IGNORECASE | re.DOTALL,
)
DESKTOP_PROMISE_AUDIT_TRANSCRIPT_RE = re.compile(
    r"\b(?:meto\s+en\s+pr[oó]xima\s+release|lo\s+incluyo|lo\s+a[nñ]adir[eé]|"
    r"spec\s+en\s+escritorio|grep\s+(?:en\s+)?transcript|transcript\s+grep|"
    r"promesas?\s+abiertas?|open\s+promises?)\b",
    re.IGNORECASE,
)
DESKTOP_PROMISE_AUDIT_BUNDLE_RE = re.compile(
    r"\b(?:dist/release|dist\\release|app\.asar|resources|bundle\s+empaquetado|"
    r"packaged\s+bundle|win-unpacked|mac(?:-arm64)?|\.dmg|\.exe)\b",
    re.IGNORECASE,
)
DESKTOP_PROMISE_AUDIT_RESOLUTION_RE = re.compile(
    r"\b(?:0\s+promesas?\s+abiertas?|0\s+open\s+promises?|sin\s+promesas?\s+abiertas?|"
    r"no\s+promises?\s+pending|followup(?:s)?\s+(?:cread[oa]s?|persistid[oa]s?)|"
    r"\bNF-[A-Z0-9][A-Z0-9-]*\b)\b",
    re.IGNORECASE,
)
PRECLOSE_READY_CLAIM_RE = re.compile(
    r"\b(?:verificad[oa]s?|preparad[oa]s?|list[oa]s?|ready|verified|prepared)\b",
    re.IGNORECASE,
)
PRECLOSE_PUBLIC_SURFACE_RE = re.compile(
    r"\b(?:url|urls|landing|landings|p[uú]blic[ao]s?|shopify|google\s+ads|rsa|campaign|campaña|manifest|web)\b",
    re.IGNORECASE,
)
PRECLOSE_VARIANT_SCOPE_RE = re.compile(
    r"\b(?:variante|variantes|marca|marcas|idioma|idiomas|incompatible|incompatibles|sheet\s*codes?|send_to|customer_photo|rsa|headlines?|descriptions?|15\+4\+2)\b",
    re.IGNORECASE,
)
PRECLOSE_REAL_ACTION_RE = re.compile(
    r"\b(?:env[ií]os?\s+real(?:es)?|pago(?:s)?\s+real(?:es)?|send(?:s)?\s+real|real\s+send|real\s+payment|charge|cobro)\b",
    re.IGNORECASE,
)
PRECLOSE_HEAD_OK_RE = re.compile(
    r"\b(?:HEAD|curl\s+-I|HTTP\s*(?:status\s*)?(?:=|:|->)?\s*200|200\s+OK|status(?:=|:)?200)\b",
    re.IGNORECASE,
)
PRECLOSE_VARIANT_EVIDENCE_RE = re.compile(
    r"\b(?:matriz|inventario|variant(?:e|es)?|caso\s+por\s+variante|1\s+caso\s+por\s+variante|one\s+case\s+per\s+variant|15\+4\+2)\b",
    re.IGNORECASE,
)
PRECLOSE_AUTHORIZATION_RE = re.compile(
    r"\b(?:autorizaci[oó]n|autorizad[oa]|approved|approval|ok\s+expl[ií]cito|explicit\s+approval)\b",
    re.IGNORECASE,
)


def _is_trivial_evidence(text: str) -> tuple[bool, str]:
    """Return (is_trivial, reason).

    Evidence is considered trivial when it is EITHER shorter than
    R03_MIN_EVIDENCE_CHARS OR matches the single-word acknowledgment
    pattern. Both checks are structural, NOT language detection:
    length is language-agnostic, and the pattern targets the exact
    class of shortcut a caller would use ("done", "listo", "ok") as
    a filler. Learning #122 prohibits hardcoded keywords for
    *semantic* detection; this is defensive input validation on a
    known-finite set of filler tokens.
    """
    stripped = (text or "").strip()
    if not stripped:
        return True, "empty"
    if R03_TRIVIAL_EVIDENCE_PATTERN.match(stripped):
        return True, "single_filler_word"
    if len(stripped) < R03_MIN_EVIDENCE_CHARS:
        return True, f"too_short (<{R03_MIN_EVIDENCE_CHARS} chars, got {len(stripped)})"
    return False, ""


def _has_correction_no_learning_justification(*parts: str) -> bool:
    """Return true when close payload explicitly justifies not persisting a learning."""
    text = " ".join(str(part or "").strip() for part in parts if str(part or "").strip())
    if len(text) < 60:
        return False
    lowered = text.lower()
    has_justification_marker = any(
        marker in lowered
        for marker in (
            "justificacion",
            "justificación",
            "justified",
            "justification",
            "no reusable learning",
            "no aprendizaje reutilizable",
        )
    )
    has_no_learning_reason = any(
        marker in lowered
        for marker in (
            "no aprendizaje",
            "sin aprendizaje",
            "no learning",
            "no reusable",
            "no reutilizable",
            "falso positivo",
            "false positive",
            "sin cambio de regla",
            "no canonical rule",
            "no cambia la regla",
        )
    )
    return has_justification_marker and has_no_learning_reason


def _is_medium_impact_edit_file(path: str) -> bool:
    clean = str(path or "").strip()
    if not clean:
        return False
    lowered = clean.lower()
    if not lowered.endswith(MEDIUM_IMPACT_EDIT_EXTENSIONS):
        return False
    return any(marker.lower() in lowered for marker in MEDIUM_IMPACT_EDIT_PATH_MARKERS)


def _close_payload_has_learning_trace(*parts: str) -> bool:
    text = " ".join(str(part or "").strip() for part in parts if str(part or "").strip())
    if LEARNING_TRACE_RE.search(text):
        return True
    lowered = text.lower()
    return "learning_title" in lowered and "learning_content" in lowered


def _requires_learning_after_medium_impact_edit(
    *,
    task: dict,
    clean_outcome: str,
    effective_files: list[str],
    closure_text: str,
) -> bool:
    if clean_outcome not in {"done", "partial", "failed"}:
        return False
    if str(task.get("task_type") or "").strip() not in {"edit", "execute", "delegate"}:
        return False
    candidate_files = effective_files or _parse_list(task.get("files") or "[]")
    if not any(_is_medium_impact_edit_file(path) for path in candidate_files):
        return False
    return bool(LEARNING_WORTHY_EDIT_RE.search(closure_text or ""))


def _is_repeated_symptom_p0_task(task: dict, closure_text: str) -> bool:
    combined = " ".join(
        str(task.get(field) or "")
        for field in (
            "goal",
            "area",
            "project_hint",
            "context_hint",
            "known_facts",
            "constraints",
            "evidence_refs",
            "verification_step",
        )
    )
    combined = f"{combined} {closure_text or ''}"
    return bool(REPEATED_SYMPTOM_P0_RE.search(combined))


def _missing_repeated_symptom_p0_evidence(evidence: str) -> list[str]:
    text = evidence or ""
    missing: list[str] = []
    if not P0_REPRO_TEST_RE.search(text):
        missing.append("test_reproductor_rojo_verde")
    if not P0_BACKEND_FLOW_RE.search(text):
        missing.append("verificacion_backend_curl_sql")
    if not P0_UI_EVIDENCE_RE.search(text):
        missing.append("evidencia_ui_post_fix")
    return missing


def _is_desktop_release_task(task: dict, closure_text: str) -> bool:
    del closure_text
    combined = " ".join(
        str(part or "")
        for part in (
            task.get("goal"),
            task.get("area"),
            task.get("project_hint"),
            task.get("context_hint"),
            task.get("verification_step"),
        )
    )
    return bool(DESKTOP_RELEASE_RE.search(combined))


def _missing_desktop_release_promise_audit_evidence(evidence: str) -> list[str]:
    text = evidence or ""
    missing: list[str] = []
    if not DESKTOP_PROMISE_AUDIT_TRANSCRIPT_RE.search(text):
        missing.append("transcript_promise_grep")
    if not DESKTOP_PROMISE_AUDIT_BUNDLE_RE.search(text):
        missing.append("dist_release_bundle_search")
    if not DESKTOP_PROMISE_AUDIT_RESOLUTION_RE.search(text):
        missing.append("missing_promises_followups")
    return missing


def _missing_preclose_variant_gate_evidence(scope_text: str, evidence: str) -> list[str]:
    scope = scope_text or ""
    claim_text = f"{scope}\n{evidence or ''}"
    if not PRECLOSE_READY_CLAIM_RE.search(claim_text):
        return []
    missing: list[str] = []
    if PRECLOSE_PUBLIC_SURFACE_RE.search(scope) and not PRECLOSE_HEAD_OK_RE.search(evidence or ""):
        missing.append("head_200_urls_publicas")
    if PRECLOSE_VARIANT_SCOPE_RE.search(scope) and not PRECLOSE_VARIANT_EVIDENCE_RE.search(evidence or ""):
        missing.append("matriz_variantes_con_caso_por_variante")
    if PRECLOSE_REAL_ACTION_RE.search(scope) and not PRECLOSE_AUTHORIZATION_RE.search(evidence or ""):
        missing.append("autorizacion_envios_pagos_reales")
    return missing


def _resolve_correction_requirements_with_justification(session_id: str, task_id: str, justification: str) -> int:
    clean_sid = str(session_id or "").strip()
    if not clean_sid:
        return 0
    conn = get_db()
    cursor = conn.execute(
        """UPDATE session_correction_requirements
           SET status = 'resolved',
               resolved_at = datetime('now'),
               resolved_learning_id = NULL
           WHERE session_id = ? AND status = 'open'""",
        (clean_sid,),
    )
    conn.commit()
    if cursor.rowcount:
        resolved = resolve_protocol_debts(
            session_id=clean_sid,
            task_id=task_id,
            debt_types=["missing_learning_after_correction"],
            resolution=f"No-learning justification accepted: {justification[:240]}",
        )
        if not resolved:
            resolve_protocol_debts(
                session_id=clean_sid,
                debt_types=["missing_learning_after_correction"],
                resolution=f"No-learning justification accepted: {justification[:240]}",
            )
    return int(cursor.rowcount or 0)


def _existing_analyze_artifact_paths(refs: list[str]) -> list[Path]:
    paths_found: list[Path] = []
    seen: set[str] = set()
    for ref in refs:
        clean = str(ref or "").strip()
        if not clean or clean.lower().startswith("followup_id"):
            continue
        if ":" in clean and not clean.startswith("/"):
            prefix, value = clean.split(":", 1)
            if prefix.strip().lower() in {"file", "path", "artifact", "report"}:
                clean = value.strip()
        candidate = Path(os.path.expanduser(clean))
        if not candidate.is_file() or candidate.suffix.lower() not in ANALYZE_ARTIFACT_SUFFIXES:
            continue
        resolved = str(candidate.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        paths_found.append(candidate)
    return paths_found


def _count_p0_p1_findings(paths_found: list[Path]) -> tuple[int, list[dict]]:
    total = 0
    artifacts: list[dict] = []
    for path in paths_found:
        findings = 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if P0_P1_FINDING_PATTERN.search(line):
                        findings += 1
        except OSError:
            continue
        if findings:
            total += findings
            artifacts.append({"path": str(path), "findings": findings})
    return total, artifacts


def _count_followup_refs(refs: list[str]) -> int:
    seen: set[str] = set()
    for ref in refs:
        for match in FOLLOWUP_REF_PATTERN.findall(str(ref or "")):
            seen.add(match.upper())
    return len(seen)


def _external_real_world_text(task: dict, *parts: str) -> str:
    fields = [
        task.get("goal", ""),
        task.get("area", ""),
        task.get("project_hint", ""),
        task.get("context_hint", ""),
        task.get("verification_step", ""),
    ]
    fields.extend(part for part in parts if part)
    return " ".join(str(part or "") for part in fields).lower()


def _requires_external_real_world_check(task: dict, *parts: str) -> bool:
    if str(task.get("task_type") or "").strip() not in ACTION_TASKS:
        return False
    text = _external_real_world_text(task, *parts)
    if _is_local_only_followup_runner_close(task, text):
        return False
    return any(
        _contains_external_action_keyword(text, keyword)
        for keyword in EXTERNAL_REAL_WORLD_ACTION_KEYWORDS
    )


def _is_local_only_followup_runner_close(task: dict, text: str) -> bool:
    area = str(task.get("area") or "").lower()
    goal = str(task.get("goal") or "").lower()
    if "followup-runner" not in f"{area} {goal} {text}":
        return False

    local_only_markers = (
        "sin envio externo",
        "sin envío externo",
        "no envio externo",
        "no envío externo",
        "no external send",
        "no external delivery",
        "internal followup-runner",
        "followup-runner interna",
        "followup-runner internas",
        "solo lectura db",
        "solo lectura de db",
        "solo lectura bd",
        "solo lectura de bd",
        "archivos locales",
        "local files",
        "db/archivos locales",
        "db/local files",
    )
    return any(marker in text for marker in local_only_markers)


def _has_external_real_world_evidence(text: str) -> bool:
    lowered = str(text or "").lower()
    if _is_trivial_evidence(lowered)[0]:
        return False
    has_verify_verb = any(keyword in lowered for keyword in REAL_WORLD_VERIFICATION_VERBS)
    has_artifact = any(keyword in lowered for keyword in REAL_WORLD_ARTIFACT_KEYWORDS)
    return has_verify_verb and has_artifact


def _contains_external_action_keyword(text: str, keyword: str) -> bool:
    clean_text = str(text or "").lower()
    clean_keyword = str(keyword or "").lower().strip()
    if not clean_text or not clean_keyword:
        return False
    return re.search(
        rf"(?<![a-z0-9]){re.escape(clean_keyword)}(?![a-z0-9])",
        clean_text,
        re.IGNORECASE,
    ) is not None


ACTION_TASKS = {"edit", "execute", "delegate"}
RESPONSE_TASKS = {"answer", "analyze"}
_GUARD_TOUCH_DEBT_TYPES = {
    "strict_protocol_write_without_guard_ack",
    "conditioned_file_touch_without_guard_ack",
    "write_without_file_guard_check",
}
EXTERNAL_REAL_WORLD_ACTION_KEYWORDS = {
    "email",
    "e-mail",
    "gmail",
    "correo",
    "mail",
    "message",
    "mensaje",
    "whatsapp",
    "telegram",
    "sms",
    "calendar",
    "calendario",
    "event",
    "evento",
    "invite",
    "invitation",
    "invitacion",
    "invitación",
    "meet",
    "zoom",
    "booking",
    "reserva",
    "send",
    "sent",
    "enviar",
    "enviado",
    "enviada",
    "client",
    "cliente",
    "family",
    "familia",
}
REAL_WORLD_VERIFICATION_VERBS = {
    "verified",
    "verify",
    "checked",
    "rechecked",
    "re-read",
    "reread",
    "opened",
    "inspected",
    "confirmed",
    "verificado",
    "verifique",
    "verifiqué",
    "comprobado",
    "comprobe",
    "comprobé",
    "revisado",
    "revise",
    "revisé",
    "abierto",
    "abri",
    "abrí",
    "confirmado",
}
REAL_WORLD_ARTIFACT_KEYWORDS = {
    "sent folder",
    "sent item",
    "message-id",
    "email",
    "correo",
    "destinatario",
    "recipient",
    "cc",
    "bcc",
    "subject",
    "asunto",
    "body",
    "cuerpo",
    "firma",
    "signature",
    "calendar",
    "calendario",
    "event",
    "evento",
    "invitee",
    "invitado",
    "meet link",
    "meet",
    "zoom",
    "booking",
    "reserva",
    "sent",
    "enviado",
}
HIGH_STAKES_KEYWORDS = {
    "medical",
    "legal",
    "financial",
    "billing",
    "invoice",
    "payment",
    "credential",
    "password",
    "security",
    "production",
    "deploy",
    "release",
    "launch",
    "delete",
    "migration",
    "pricing",
    "refund",
    "customer",
    "public",
    "brand",
    "reputation",
    "reputational",
    "roadmap",
    "revenue",
    "cost",
}
# v5.2.0: Spanish high-stakes keywords. Parity with the English set so a
# goal written in Spanish ("migrar producción a nuevo servidor") trips
# the same high-stakes gate as its English twin. Accented and unaccented
# variants are both listed because user prompts mix both freely.
HIGH_STAKES_KEYWORDS_ES = {
    "crítico",
    "critico",
    "crítica",
    "critica",
    "producción",
    "produccion",
    "cliente",
    "clientes",
    "despliegue",
    "desplegar",
    "pago",
    "pagos",
    "facturación",
    "facturacion",
    "factura",
    "credencial",
    "credenciales",
    "contraseña",
    "seguridad",
    "legal",
    "médico",
    "medico",
    "financiero",
    "financiera",
    "privacidad",
    "marca",
    "reputación",
    "reputacion",
    "ingresos",
    "borrar",
    "eliminar",
    "migración",
    "migracion",
    "migrar",
    "lanzamiento",
    "lanzar",
    "precio",
    "precios",
    "reembolso",
    "público",
    "publico",
    "riesgo",
    "riesgos",
    "coste",
    "costes",
    "ventas",
    "pedido",
    "pedidos",
}
# v5.2.0: Negation patterns that should SUPPRESS the high-stakes flag.
# Without this, a user message like "sin afectar producción" or
# "no tocar prod" triggers a false positive just because the keyword
# is physically present. Bilingual and conservative on purpose.
NEGATION_PATTERNS = (
    re.compile(r"\bno\s+tocar\s+prod(?:ucci[oó]n|uccion)?\b", re.IGNORECASE),
    re.compile(r"\bsin\s+(?:tocar|afectar|romper|modificar)\b", re.IGNORECASE),
    re.compile(r"\bnunca\s+(?:borrar|eliminar|tocar)\b", re.IGNORECASE),
    re.compile(r"\bno\s+(?:borrar|eliminar|tocar|modificar)\b", re.IGNORECASE),
    re.compile(r"\bevitar\s+(?:borrar|eliminar|tocar|romper)\b", re.IGNORECASE),
    re.compile(r"\bavoid\s+(?:deleting|touching|breaking|modifying)\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+(?:touch|break|modify|delete)\b", re.IGNORECASE),
    re.compile(r"\bwithout\s+(?:touching|breaking|affecting)\b", re.IGNORECASE),
)

TASK_TYPE_ALIASES = {
    "create": "edit",
}

CLOSE_OUTCOME_ALIASES = {
    "complete": "done",
    "completed": "done",
    "deployed": "done",
    "published": "done",
}

HIGH_STAKES_OVERRIDE_TRUE = {"high", "critical", "elevated"}
HIGH_STAKES_OVERRIDE_FALSE = {
    "low",
    "normal",
    "routine",
    "none",
    "off",
    "false",
    "read-only",
    "readonly",
    "dry-run",
    "dryrun",
}
TASK_OPEN_LOCAL_CONTEXT_TRUE_VALUES = {"1", "true", "yes", "y", "on", "force"}

_INTERNAL_AUDIT_AREAS = {
    "guardian",
    "protocol",
    "tests",
    "nexo-ops",
}
_INTERNAL_AUDIT_MARKERS = (
    "read-only",
    "readonly",
    "dry-run",
    "dry run",
    "protocol audit",
    "guardian audit",
    "guard audit",
    "contract test",
    "test harness",
    "heuristic",
    "detector",
    "simulated",
    "simulation",
    "fixture",
)
_INTERNAL_AUDIT_VERBS = {
    "audit",
    "review",
    "test",
    "testing",
    "verify",
    "debug",
    "probe",
    "inspect",
}

_RELEASE_AREA_HINTS = {
    "release",
    "deploy",
    "deployment",
    "publish",
    "publishing",
    "gh-pages",
}
_RELEASE_GOAL_HINTS = {
    "release",
    "deploy",
    "launch",
    "ship",
    "publish",
    "tag",
    "rollout",
    "despliegue",
    "lanzamiento",
    "publicar",
    "etiqueta",
}
_RELEASE_CONTEXT_HINTS = {
    "production",
    "staging",
    "changelog",
    "version",
    "update.json",
    "npm",
    "gh-pages",
    "prod",
    "producción",
    "produccion",
    "cambios",
}

_GOAL_PLAN_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s+|\d+[.)]\s+|(?:paso|step)\s*\d+\s*:\s+)",
    re.IGNORECASE,
)


def _parse_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return [str(value).strip()]


def _extract_plan_from_goal(goal: str) -> list[str]:
    """Lift explicit numbered/bulleted steps embedded in `goal`.

    This is intentionally structural, not semantic: it only activates when
    the goal already contains clear plan lines (e.g. `1. inspect`, `- patch`,
    `Paso 2: verify`). It prevents `task_open` from complaining about a
    missing plan when the caller already wrote one inside the goal text.
    """
    if not goal:
        return []
    extracted: list[str] = []
    for raw_line in str(goal).splitlines():
        if not raw_line.strip():
            continue
        match = _GOAL_PLAN_LINE_RE.match(raw_line)
        if not match:
            continue
        step = raw_line[match.end():].strip()
        if step:
            extracted.append(step)
    return extracted if len(extracted) >= 2 else []


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _task_open_local_context_enabled() -> bool:
    """Keep heavy local-context routing off the task_open critical path by default."""
    return (
        os.environ.get("NEXO_TASK_OPEN_LOCAL_CONTEXT", "").strip().lower()
        in TASK_OPEN_LOCAL_CONTEXT_TRUE_VALUES
    )


def _parse_int_list(value) -> list[int]:
    items = _parse_list(value)
    parsed: list[int] = []
    for item in items:
        try:
            parsed.append(int(item))
        except (TypeError, ValueError):
            continue
    return parsed


def _has_negation_context(text: str) -> bool:
    """Return True when the text explicitly disclaims touching the sensitive area.

    Used to suppress high-stakes false positives where the user is stating
    the *boundary* of safe work ("without touching production") rather than
    the *target* of a risky action ("migrate production").
    """
    if not text:
        return False
    return any(pattern.search(text) for pattern in NEGATION_PATTERNS)


def _detect_high_stakes(*parts: str) -> bool:
    combined = " ".join((part or "").strip().lower() for part in parts if part)
    if not combined:
        return False
    # Negation override: "sin afectar producción" / "don't touch prod" / etc.
    # Explicit disclaimers suppress the flag even if a high-stakes keyword
    # is physically present, otherwise boundary statements get miscategorised
    # as action targets.
    if _has_negation_context(combined):
        return False
    return any(
        keyword in combined
        for keyword in HIGH_STAKES_KEYWORDS | HIGH_STAKES_KEYWORDS_ES
    )


def _parse_high_stakes_override(stakes: str) -> bool | None:
    clean = (stakes or "").strip().lower()
    if not clean:
        return None
    if clean in HIGH_STAKES_OVERRIDE_TRUE:
        return True
    if clean in HIGH_STAKES_OVERRIDE_FALSE:
        return False
    return None


def _has_internal_audit_context(
    *,
    goal: str,
    area: str = "",
    context_hint: str = "",
    verification_step: str = "",
    constraints=None,
) -> bool:
    combined = " ".join(
        part.strip().lower()
        for part in [
            goal or "",
            area or "",
            context_hint or "",
            verification_step or "",
            " ".join(_parse_list(constraints)),
        ]
        if part and str(part).strip()
    )
    if not combined:
        return False
    if any(marker in combined for marker in _INTERNAL_AUDIT_MARKERS):
        return True
    area_lower = (area or "").strip().lower()
    if area_lower not in _INTERNAL_AUDIT_AREAS:
        return False
    return any(token in combined for token in _INTERNAL_AUDIT_VERBS)


def _decision_support_required(*, task_type: str, high_stakes: bool) -> bool:
    return task_type in ACTION_TASKS and high_stakes


def _is_release_task(*, goal: str, area: str = "", project_hint: str = "", verification_step: str = "") -> bool:
    area_lower = (area or "").strip().lower()
    if area_lower in _RELEASE_AREA_HINTS:
        return True
    if any(area_lower.startswith(f"{hint}-") for hint in _RELEASE_AREA_HINTS):
        return True

    goal_lower = (goal or "").strip().lower()
    if not goal_lower:
        return False
    if not any(token in goal_lower for token in _RELEASE_GOAL_HINTS):
        return False

    combined_context = " ".join(
        part.strip().lower()
        for part in [area, project_hint, verification_step, goal]
        if part and str(part).strip()
    )
    return any(token in combined_context for token in _RELEASE_CONTEXT_HINTS)


def _requires_live_surface_verification(task: dict, outcome: str) -> bool:
    if outcome != "done":
        return False
    clean_type = str(task.get("task_type") or "").strip()
    if clean_type not in {"edit", "execute"}:
        return False
    combined = " ".join(
        str(task.get(field) or "")
        for field in ("goal", "area", "project_hint", "context_hint", "verification_step")
    ).lower()
    if not combined:
        return False
    subject_hit = re.search(
        r"\b(bug|estado|state|storefront|backend|front[- ]?end|datos|data|bd|database|producci[oó]n|production|live)\b",
        combined,
    )
    behavior_hit = re.search(
        r"\b(arregl|fix|resolver|resuelto|declara|afirm|verificar|validar|confirmar|close|cerrar)\b",
        combined,
    )
    return bool(subject_hit and behavior_hit)


def _has_live_surface_verification(evidence: str) -> bool:
    text = (evidence or "").lower()
    if not text:
        return False
    live_markers = (
        "black-box.ndjson",
        "impossible_state_recovered",
        "playwright",
        "browser",
        "navegador",
        "screenshot",
        "captura",
        "tema publicado",
        "published theme",
        "theme_id",
        "cloud sql",
        "production db",
        "bd producción",
        "bd de producción",
        "mysql -h",
        "psql ",
        "gcloud logs",
        "live logs",
        "logs vivos",
        "http 200",
        "curl ",
        "url pública",
        "public url",
    )
    if any(marker in text for marker in live_markers):
        return True
    return bool(re.search(r"\b(producci[oó]n|production|live)\b.*\b(logs?|bd|db|browser|navegador|playwright|url|http|tema)\b", text))


UI_CHANGE_HINT_RE = re.compile(
    r"\b(ui|ux|front[- ]?end|frontend|renderer|web|browser|navegador|pantalla|modal|selector|bot[oó]n|button|css|html|react|vue|electron|theme|tema|storefront)\b",
    re.IGNORECASE,
)

RELEASE_READY_CLAIM_RE = re.compile(
    r"\b(release\s+lista|feature\s+completa|desplegado\s+ok|despliegue\s+ok|lista\s+para\s+release|release\s+ready|feature\s+complete|deployed\s+ok)\b",
    re.IGNORECASE,
)

ORIGINAL_SYMPTOM_REPRO_RE = re.compile(
    r"\b(s[ií]ntoma|symptom|original|repro|reproduj|reproduc|reportad[oa]|observad[oa])\b",
    re.IGNORECASE,
)

ORIGINAL_SYMPTOM_EVIDENCE_RE = re.compile(
    r"\b(curl|http\s*200|url\s+(?:p[uú]blica|repro)|screenshot|captura|headed|browser|navegador|playwright)\b",
    re.IGNORECASE,
)


def _requires_original_symptom_verification(task: dict, closure_text: str) -> bool:
    if not RELEASE_READY_CLAIM_RE.search(closure_text or ""):
        return False
    combined = " ".join(
        str(task.get(field) or "")
        for field in ("goal", "area", "project_hint", "context_hint", "verification_step", "files")
    )
    return bool(UI_CHANGE_HINT_RE.search(combined))


def _has_original_symptom_verification(evidence: str) -> bool:
    text = evidence or ""
    return bool(ORIGINAL_SYMPTOM_REPRO_RE.search(text) and ORIGINAL_SYMPTOM_EVIDENCE_RE.search(text))


def _requires_production_change_log(
    task: dict,
    outcome: str,
    evidence: str,
    change_summary: str,
    triggered_by: str,
) -> bool:
    if outcome not in {"done", "partial", "failed"}:
        return False
    clean_type = str(task.get("task_type") or "").strip()
    if clean_type not in {"edit", "execute"}:
        return False
    task_context = " ".join(
        str(part or "")
        for part in (
            task.get("goal"),
            task.get("area"),
            task.get("project_hint"),
            task.get("context_hint"),
            task.get("verification_step"),
        )
    ).lower()
    action_context = " ".join(
        str(part or "")
        for part in (
            evidence,
            change_summary,
            triggered_by,
        )
    ).lower()
    combined = f"{task_context}\n{action_context}"
    return bool(
        re.search(
            r"\b(git push|rsync|scp|ssh|npm publish|upload-release\.sh|gcloud builds)\b",
            combined,
        )
        or re.search(r"\b(deploy|desplieg|publish|publicaci[oó]n)\b", action_context)
    )


WRITTEN_REFERENCE_DEPENDENCY_RE = re.compile(
    r"\b("
    r"deploy[-_\s]?notes|internal\s+docs?|docs?/|documentation|documentaci[oó]n|"
    r"transcripts?|transcripciones?|session\s+diar(?:y|ies)|diarios?|notes?|notas?"
    r")\b",
    re.IGNORECASE,
)
STALE_WRITTEN_REFERENCE_RE = re.compile(
    r"("
    r">\s*24h|24\+?\s*(?:h|hours?|horas?)|older\s+than\s+24\s*(?:h|hours?)|"
    r"m[aá]s\s+de\s+24\s*horas|stale|obsolet[ao]s?|viejos?|antigu[ao]s?|"
    r"yesterday|ayer|last\s+week|semana\s+pasada|"
    r"\b20\d{2}-\d{2}-\d{2}\b"
    r")",
    re.IGNORECASE,
)
LIVE_SOURCE_EVIDENCE_RE = re.compile(
    r"\b("
    r"repo|git(?:_head| status| show| log)?|commit|branch|"
    r"bd|db|database|sql|endpoint|public\s+endpoint|producci[oó]n|production|live|"
    r"curl|http\s*\d{3}|gcloud|cloud\s+run|ssh|manifest|update\.json"
    r")\b",
    re.IGNORECASE,
)


def _written_reference_requires_live_verify(
    *,
    goal: str,
    area: str,
    context_hint: str,
    constraints: list[str],
    evidence_refs: list[str],
    unknowns: list[str],
    verification_step: str,
) -> bool:
    combined = "\n".join(
        str(part or "")
        for part in [
            goal,
            area,
            context_hint,
            verification_step,
            *constraints,
            *evidence_refs,
            *unknowns,
        ]
    )
    if not WRITTEN_REFERENCE_DEPENDENCY_RE.search(combined):
        return False
    if not STALE_WRITTEN_REFERENCE_RE.search(combined):
        return False
    evidence_text = "\n".join(str(ref or "") for ref in evidence_refs)
    return not LIVE_SOURCE_EVIDENCE_RE.search(evidence_text)


def evaluate_response_confidence(
    *,
    goal: str,
    task_type: str,
    area: str = "",
    context_hint: str = "",
    constraints=None,
    evidence_refs=None,
    unknowns=None,
    verification_step: str = "",
    stakes: str = "",
    pre_action_context_hits: int = 0,
    area_has_atlas_entry: bool = False,
) -> dict:
    evidence_refs = _parse_list(evidence_refs)
    unknowns = _parse_list(unknowns)
    constraints = _parse_list(constraints)
    explicit_stakes = (stakes or "").strip().lower()

    reasons: list[str] = []
    stakes_override = _parse_high_stakes_override(explicit_stakes)
    if stakes_override is not None:
        high_stakes = stakes_override
        if stakes_override:
            reasons.append("stakes override forces high-stakes handling")
        else:
            reasons.append("stakes override suppresses automatic high-stakes detection")
    else:
        detected_high_stakes = _detect_high_stakes(
            goal,
            area,
            context_hint,
            " ".join(constraints),
            explicit_stakes,
        )
        if detected_high_stakes and _has_internal_audit_context(
            goal=goal,
            area=area,
            context_hint=context_hint,
            verification_step=verification_step,
            constraints=constraints,
        ):
            high_stakes = False
            reasons.append("internal audit/testing context suppresses automatic high-stakes detection")
        else:
            high_stakes = detected_high_stakes

    score = 85
    if unknowns:
        score -= 35
        reasons.append(f"{len(unknowns)} unknown(s) still unresolved")
    if not evidence_refs:
        score -= 25
        reasons.append("no evidence_refs supplied")
    if not verification_step.strip():
        score -= 10
        reasons.append("no verification_step defined")
    if high_stakes:
        score -= 20
        reasons.append("high-stakes context detected")
    stale_written_reference_without_live = _written_reference_requires_live_verify(
        goal=goal,
        area=area,
        context_hint=context_hint,
        constraints=constraints,
        evidence_refs=evidence_refs,
        unknowns=unknowns,
        verification_step=verification_step,
    )
    if stale_written_reference_without_live:
        score -= 30
        reasons.append("stale written reference requires live source verification")

    # v5.2.0: Positive signals. Before this release the score was purely
    # a penalty accumulator — there was no way to reward tasks that had
    # meaningful prior context loaded or that sat inside a known area.
    # Cap at +10 and +5 so these can never override a real risk signal.
    if pre_action_context_hits > 0:
        boost = min(10, pre_action_context_hits * 2)
        score += boost
        reasons.append(
            f"+{boost} from {pre_action_context_hits} pre-action context hit(s)"
        )
    if area_has_atlas_entry:
        score += 5
        reasons.append("+5 from known project-atlas area")

    final_score = max(0, min(100, score))

    mode = "answer"
    if task_type in RESPONSE_TASKS:
        if high_stakes and (unknowns or not evidence_refs):
            mode = "defer"
        elif unknowns:
            mode = "ask"
        elif high_stakes or not evidence_refs or not verification_step.strip():
            mode = "verify"
        if mode == "answer" and stale_written_reference_without_live:
            mode = "verify"

        # v5.2.0: Numeric safeguard. The boolean decision tree above
        # covers every obvious case, but tasks can accumulate soft
        # penalties without tripping any single rule. When the final
        # score is critically low, downgrade the mode by one step.
        # This catches edge cases and is monotonic — it can only make
        # the response discipline stricter, never looser.
        if mode == "answer" and final_score < 50:
            mode = "verify"
            reasons.append(
                f"numeric safeguard: score {final_score} < 50 forces verify"
            )
        elif mode == "verify" and final_score < 30 and high_stakes:
            mode = "defer"
            reasons.append(
                f"numeric safeguard: high-stakes with score {final_score} forces defer"
            )

    next_action = {
        "answer": "You may answer directly, but stay within the evidence you actually have.",
        "verify": "Verify the claim with concrete evidence before answering.",
        "ask": "Ask for the missing information instead of guessing.",
        "defer": "Do not answer yet. Defer until you have evidence and a verification path.",
    }[mode]

    return {
        "mode": mode,
        "confidence": final_score,
        "high_stakes": high_stakes,
        "reasons": reasons,
        "next_action": next_action,
    }


def _guard_excerpt(text: str, max_lines: int = 12) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return "\n".join(lines[:max_lines])


def _extract_guard_blocking_ids(guard_summary: str) -> list[int]:
    ids: list[int] = []
    in_blocking = False
    for raw_line in (guard_summary or "").splitlines():
        line = raw_line.strip()
        if line.startswith("BLOCKING RULES"):
            in_blocking = True
            continue
        if in_blocking and not line:
            break
        if in_blocking:
            match = re.search(r"#(\d+)", line)
            if match:
                ids.append(int(match.group(1)))
    return ids


def _auto_followup_id() -> str:
    return f"NF-PROTOCOL-{int(time.time())}-{secrets.randbelow(100000)}"


def _ensure_followup(description: str, *, verification: str = "", reasoning: str = "") -> dict:
    conn = get_db()
    row = conn.execute(
        """SELECT id
           FROM followups
           WHERE status NOT LIKE 'COMPLETED%'
             AND status NOT IN ('DELETED', 'archived', 'blocked', 'waiting')
             AND description = ?
           LIMIT 1""",
        (description,),
    ).fetchone()
    if row:
        return {"id": row["id"], "created": False}
    # Content fingerprint for deterministic followup id — not security-sensitive.
    followup_id = f"NF-PROTOCOL-{hashlib.sha1(description.encode('utf-8'), usedforsecurity=False).hexdigest()[:10].upper()}"
    result = create_followup(
        followup_id,
        description,
        verification=verification,
        reasoning=reasoning,
    )
    if result and "error" not in result:
        return {"id": result.get("id", followup_id), "created": True}
    return {"id": "", "created": False, "error": result.get("error", "followup create failed") if isinstance(result, dict) else "followup create failed"}


def _attention_snapshot(session_id: str) -> dict:
    goals = [goal for goal in list_workflow_goals(include_closed=False, limit=50) if goal.get("session_id") == session_id]
    runs = [run for run in list_workflow_runs(include_closed=False, limit=50) if run.get("session_id") == session_id]

    active_goals = [goal for goal in goals if goal.get("status") == "active"]
    blocked_goals = [goal for goal in goals if goal.get("status") == "blocked"]
    waiting_runs = [run for run in runs if run.get("status") in {"blocked", "waiting_approval"}]

    status = "focused"
    warnings: list[str] = []
    recommended_action = "Current focus load is acceptable."

    if len(active_goals) >= 4 or len(runs) >= 5:
        status = "overloaded"
        warnings.append("Too many active goals or open workflow runs are competing for attention.")
        recommended_action = "Finish, block, or abandon one active goal before opening more execution work."
    elif len(active_goals) >= 2 or len(runs) >= 3 or len(waiting_runs) >= 2:
        status = "split"
        warnings.append("Attention is split across multiple active goals or waiting workflow runs.")
        recommended_action = "Narrow focus and make one next action explicit before expanding scope."

    return {
        "status": status,
        "active_goals": len(active_goals),
        "blocked_goals": len(blocked_goals),
        "open_runs": len(runs),
        "waiting_runs": len(waiting_runs),
        "warnings": warnings,
        "recommended_action": recommended_action,
        "top_goal_titles": [goal.get("title", "") for goal in active_goals[:3]],
    }


def _preview_prospective_triggers(goal: str, context_hint: str, files_list: list[str]) -> list[dict]:
    text = " | ".join(part for part in [goal, context_hint, " ".join(files_list)] if part).strip()
    if not text:
        return []
    try:
        import cognitive
    except Exception:
        return []
    try:
        matches = cognitive.preview_triggers(text, use_semantic=False)
    except Exception:
        return []
    return [
        {
            "id": match["id"],
            "pattern": match["pattern"],
            "action": match["action"],
            "context": match.get("context", ""),
            "match_type": match.get("match_type", "keyword"),
        }
        for match in matches
    ]


ATLAS_PATH = os.path.join(
    os.environ.get("NEXO_HOME", os.path.join(os.path.expanduser("~"), ".nexo")),
    "brain",
    "project-atlas.json",
)


def _builtin_area_context(area: str) -> dict | None:
    """Return atlas-like context for core runtime areas not modelled as projects.

    Some areas are real product surfaces but do not belong in project-atlas
    because they are shared runtime capabilities rather than business projects.
    Personal scripts is the main example: the agent still needs a canonical
    source of truth for paths and artifacts instead of getting `atlas_entry: null`.
    """
    clean_area = (area or "").strip().lower()
    normalized = clean_area.replace("_", "-")
    if normalized not in {"personal-scripts", "personal-script"}:
        return None

    scripts_dir = paths.personal_scripts_dir()
    return {
        "project_key": "personal-scripts",
        "description": (
            "User-owned runtime scripts. Keep custom automations here instead of "
            "editing core shipped scripts or LaunchAgents directly."
        ),
        "locations": {
            "scripts_dir": str(scripts_dir),
            "registry_db": str(paths.brain_dir() / "personal_scripts.db"),
            "launchagents_dir": str(Path.home() / "Library" / "LaunchAgents"),
        },
        "servers": {},
    }


def _build_area_context(area: str) -> dict:
    """Build a pre-reading context block for a known area.

    Returns project-atlas entry, recent area learnings, and active area followups
    so the agent never starts 'cold' on a known project.
    """
    clean_area = (area or "").strip().lower()
    if not clean_area:
        return {"has_context": False}

    # 1. Project-atlas lookup
    atlas_entry = _builtin_area_context(clean_area)
    try:
        if atlas_entry is None:
            with open(ATLAS_PATH, "r", encoding="utf-8") as f:
                atlas = json.load(f)
            for key, entry in atlas.items():
                if key == "_meta":
                    continue
                aliases = [a.lower() for a in entry.get("aliases", [])]
                if clean_area == key.lower() or clean_area in aliases:
                    atlas_entry = {
                        "project_key": key,
                        "description": entry.get("description", ""),
                        "locations": entry.get("locations", {}),
                        "servers": {k: {sk: sv for sk, sv in v.items() if sk != "credential_key"} for k, v in entry.get("servers", {}).items()} if isinstance(entry.get("servers"), dict) else {},
                    }
                    break
    except Exception:
        pass

    # 2. Recent area learnings (top 5)
    area_learnings = []
    try:
        results = search_learnings(clean_area, category=clean_area)
        if not results:
            results = search_learnings(clean_area)
        for learning in results[:5]:
            area_learnings.append({
                "id": learning.get("id"),
                "title": (learning.get("title") or "")[:120],
                "priority": learning.get("priority", "medium"),
            })
    except Exception:
        pass

    # 3. Active followups for the area (keyword match on description)
    area_followups = []
    try:
        all_active = get_followups("active")
        for followup in all_active:
            desc = (followup.get("description") or "").lower()
            fid = (followup.get("id") or "").lower()
            if clean_area in desc or clean_area in fid:
                area_followups.append({
                    "id": followup.get("id"),
                    "description": (followup.get("description") or "")[:120],
                    "date": followup.get("date"),
                    "priority": followup.get("priority", "medium"),
                })
                if len(area_followups) >= 5:
                    break
    except Exception:
        pass

    has_context = bool(atlas_entry or area_learnings or area_followups)
    return {
        "has_context": has_context,
        "area": clean_area,
        "atlas_entry": atlas_entry,
        "learnings_count": len(area_learnings),
        "learnings": area_learnings,
        "followups_count": len(area_followups),
        "followups": area_followups,
    }


def _create_preventive_followup(goal: str, *, attention: dict, warnings: list[dict]) -> dict | None:
    warning_lines: list[str] = []
    for match in warnings[:2]:
        action = str(match.get("action") or "").strip()
        if action:
            warning_lines.append(action[:120])
    if attention.get("warnings"):
        warning_lines.append(str(attention["warnings"][0])[:120])
    warning_lines = [line for idx, line in enumerate(warning_lines) if line and line not in warning_lines[:idx]]
    if not warning_lines:
        return None
    description = (
        f"Preventive followup before continuing '{goal[:90]}': "
        + " | ".join(warning_lines[:3])
    )
    reasoning = (
        "Created automatically during task_open because NEXO detected pre-failure warning signals "
        "before execution started."
    )
    verification = (
        "Pre-failure warning resolved or explicitly acknowledged through durable goals/workflows before continuing"
    )
    return _ensure_followup(description, verification=verification, reasoning=reasoning)


def _create_missing_learning_followup(task: dict, task_id: str, effective_files: list[str]) -> dict:
    target = ", ".join(effective_files[:3]) if effective_files else (task.get("goal", "")[:120] or task_id)
    description = (
        f"Capture reusable learning from corrected task {task_id}: "
        f"turn the fix around {target} into one canonical learning and supersede conflicting rules if needed."
    )
    reasoning = (
        f"Protocol task {task_id} was marked as corrected but closed without a reusable learning. "
        f"Prevent losing the fix or leaving contradictory active rules behind."
    )
    return create_followup(
        (_auto_followup_id()).strip(),
        description,
        verification="Learning captured and conflicting rule lifecycle resolved",
        reasoning=reasoning,
    )


def _capture_learning(
    task: dict,
    task_id: str,
    effective_files: list[str],
    *,
    category: str,
    title: str,
    content: str,
    reasoning: str,
    priority: str = "high",
    prevention: str = "",
    applies_to_override: str = "",
    source_authority: str = "explicit_instruction",
) -> dict:
    from tools_learnings import find_conflicting_active_learning, handle_learning_add

    clean_title = (title or "").strip()[:120]
    clean_content = (content or "").strip()
    clean_reasoning = (reasoning or f"Captured from protocol task {task_id}").strip()
    applies_to = applies_to_override.strip() if applies_to_override.strip() else ",".join(effective_files)
    if not clean_title or not clean_content:
        return {"ok": False, "error": "insufficient context for learning capture"}

    conflicting = find_conflicting_active_learning(
        category=category,
        title=clean_title,
        content=clean_content,
        applies_to=applies_to,
    )
    supersedes_id = int(conflicting["id"]) if conflicting else 0
    response = handle_learning_add(
        category=category,
        title=clean_title,
        content=clean_content,
        reasoning=clean_reasoning,
        prevention=prevention,
        applies_to=applies_to,
        priority=priority,
        supersedes_id=supersedes_id,
        source_authority=source_authority,
    )
    match = re.search(r"Learning #(\d+) added", response)
    if match:
        return {
            "ok": True,
            "id": int(match.group(1)),
            "response": response,
            "superseded_id": supersedes_id or None,
        }
    # A near/exact duplicate is a SUCCESSFUL no-op merge — the learning already
    # exists and no duplicate row was created (handle_learning_add returns
    # "already exists" / "resolved as merge"). Treat it as success so idempotent
    # re-captures (e.g. the same self-detected error twice) do not report a
    # phantom learning_ok=False in the close-response telemetry.
    dedup = re.search(r"Learning #(\d+) (?:already exists|resolved as merge)", response)
    if dedup:
        return {
            "ok": True,
            "deduped": True,
            "id": int(dedup.group(1)),
            "response": response,
            "superseded_id": supersedes_id or None,
        }
    return {
        "ok": False,
        "error": response,
        "conflicting_learning_id": supersedes_id or None,
    }


def _auto_capture_learning(task: dict, task_id: str, effective_files: list[str], *,
                           clean_evidence: str, change_summary: str, change_why: str,
                           outcome_notes: str) -> dict:
    title_seed = (change_summary or task.get("goal") or f"Protocol correction {task_id}").strip()
    content_parts = []
    if change_why.strip():
        content_parts.append(change_why.strip())
    elif task.get("goal"):
        content_parts.append(str(task.get("goal", "")).strip())
    if outcome_notes.strip():
        content_parts.append(outcome_notes.strip())
    if clean_evidence.strip():
        content_parts.append(f"Verification evidence: {clean_evidence.strip()}")
    if effective_files:
        content_parts.append(f"Affected files: {', '.join(effective_files[:5])}")

    title = title_seed[:120]
    content = " ".join(part for part in content_parts if part).strip()
    return _capture_learning(
        task,
        task_id,
        effective_files,
        category=(task.get("area") or "nexo-ops"),
        title=title,
        content=content,
        reasoning=f"Auto-captured from corrected protocol task {task_id}.",
        priority="high",
    )


# ── Forgotten-step followup detector (objective omission markers) ──────
_FORGOTTEN_STEP_FOLLOWUP_RE = re.compile(
    r"\b(?:forgot|forgotten|missed|omitted|never (?:created|added|set up|configured|deployed|ran)|"
    r"missing (?:the )?(?:cron|step|trigger|hook|migration|index|webhook|deploy)|"
    r"olvid[éeè]|me olvid[éeè]|falt[óoa]ba?|no se (?:cre[óo]|configur[óo]|despleg[óo]|registr[óo]))\b",
    re.IGNORECASE,
)


def _followup_signals_forgotten_step(*descriptions: object) -> bool:
    """True only when a followup description objectively states an omission.

    A generic 'verify weekly' or 'monitor X' followup must NOT count — only an
    explicit 'forgot/missing/never created the cron' style description does.
    """
    for desc in descriptions:
        text = str(desc or "").strip()
        if text and _FORGOTTEN_STEP_FOLLOWUP_RE.search(text):
            return True
    return False


def _detect_and_capture_self_error(
    task: dict,
    task_id: str,
    *,
    clean_outcome: str,
    closure_text: str,
    correction: bool,
    effective_files: list[str],
    forgotten_step_followup: bool,
    debts_created: list[dict],
) -> dict | None:
    """Ola 2 — auto-detect that a PRIOR own action was wrong and learn from it.

    Runs AFTER the current task is closed. Compares it against recently
    closed-as-done tasks; on high-confidence objective evidence it creates a
    learning with a concrete prevention rule (source_authority=code_test_evidence,
    NOT a Francisco correction). On low confidence it records a low-confidence
    candidate as an INFO protocol_debt — never a learning. Best-effort: any
    failure returns None and never blocks the close.

    Returns a small dict describing what happened (for the close response), or
    None when nothing was detected / on error.
    """
    try:
        import self_error_detector as sed
        from db import list_recent_closed_tasks

        # Only closes that actually claim progress can host / reveal a self-error.
        if clean_outcome not in {"done", "partial"}:
            return None

        prior_tasks = list_recent_closed_tasks(
            outcome="done",
            exclude_task_id=task_id,
            within_days=sed.LOOKBACK_DAYS,
            limit=sed.MAX_PRIOR_TASKS,
        )
        if not prior_tasks:
            # Nothing previously declared done → cannot have a revealed self-error
            # from file overlap. A forgotten-step followup alone is candidate-only.
            if not forgotten_step_followup:
                return None

        evaluation = sed.evaluate_self_error(
            current_task=task,
            prior_tasks=prior_tasks,
            closure_text=closure_text,
            correction_happened=correction,
            forgotten_step_followup=forgotten_step_followup,
        )

        decision = evaluation.get("decision")
        if decision == "none":
            return None

        if decision == "candidate":
            # Low-confidence: record a quiet INFO candidate, NEVER a learning.
            # Reuses the existing open-debt dedup so the same candidate does not
            # pile up across repeated closes of the same task.
            debt = _ensure_open_debt(
                task.get("session_id", ""),
                task_id,
                "self_error_candidate",
                severity="info",
                evidence=(
                    f"Low-confidence self-error candidate (confidence="
                    f"{evaluation.get('confidence')}, signal={evaluation.get('signal')}). "
                    f"{'; '.join(evaluation.get('reasons') or [])[:400]}"
                ),
                debts=debts_created,
            )
            return {
                "decision": "candidate",
                "confidence": evaluation.get("confidence"),
                "signal": evaluation.get("signal"),
                "debt_id": debt.get("id"),
            }

        # decision == "fire": create the learning with a concrete prevention.
        payload = sed.build_self_error_learning(current_task=task, evaluation=evaluation)
        learning = _capture_learning(
            task,
            task_id,
            effective_files,
            category=payload["category"],
            title=payload["title"],
            content=payload["content"],
            reasoning=payload["reasoning"],
            priority="high",
            prevention=payload["prevention"],
            applies_to_override=payload["applies_to"],
            source_authority=payload["source_authority"],
        )
        return {
            "decision": "fire",
            "confidence": evaluation.get("confidence"),
            "signal": evaluation.get("signal"),
            "prior_task_id": evaluation.get("prior_task_id"),
            "overlap_files": evaluation.get("overlap_files"),
            "learning_ok": bool(learning.get("ok")),
            "learning_id": learning.get("id"),
            "learning_error": None if learning.get("ok") else learning.get("error"),
        }
    except Exception:
        # Self-error detection is strictly best-effort; never break a close.
        return None


def _append_debt_ref(debts: list[dict], debt: dict, *, debt_type: str, severity: str):
    debt_id = debt.get("id")
    if debt_id and any(item.get("id") == debt_id for item in debts):
        return
    debts.append(
        {
            "id": debt_id,
            "debt_type": debt_type,
            "severity": severity,
        }
    )


def _ensure_open_debt(
    session_id: str,
    task_id: str,
    debt_type: str,
    *,
    severity: str,
    evidence: str,
    debts: list[dict],
) -> dict:
    existing = list_protocol_debts(
        status="open",
        task_id=task_id,
        session_id="" if task_id else session_id,
        debt_type=debt_type,
        limit=1,
    )
    debt = existing[0] if existing else create_protocol_debt(
        session_id,
        debt_type,
        severity=severity,
        task_id=task_id,
        evidence=evidence,
    )
    _append_debt_ref(debts, debt, debt_type=debt_type, severity=severity)
    return debt


def _record_debt(session_id: str, task_id: str, debt_type: str, *, severity: str, evidence: str, debts: list[dict]):
    debt = create_protocol_debt(
        session_id,
        debt_type,
        severity=severity,
        task_id=task_id,
        evidence=evidence,
    )
    _append_debt_ref(debts, debt, debt_type=debt_type, severity=severity)


TOTAL_CLOSURE_RE = re.compile(
    r"\b("
    r"sin\s+deuda|no\s+queda\s+deuda|deuda\s+cero|todo\s+cerrado|"
    r"goal\s+cumplido|objetivo\s+cumplido|completamente\s+cerrad[oa]|"
    r"no\s+queda\s+nada\s+pendiente|all\s+set|all\s+done|no\s+open\s+debt"
    r")\b",
    re.IGNORECASE,
)
PENDING_RELEASE_GATE_RE = re.compile(
    r"\b(smoke|tag|tags|merge|release|stable|broadcast|publicaci[oó]n)\b.{0,80}\b(pendiente|pending|falta|sin\s+verificar|sin\s+evidencia)\b",
    re.IGNORECASE | re.DOTALL,
)
PENDING_RELEASE_NEGATED_RE = re.compile(
    r"\b(?:0|zero)\s+(?:open\s+)?(?:promises?|items?|gates?|pendientes?)\s+pending\b|"
    r"\bno\s+(?:open\s+)?(?:promises?|items?|gates?)\s+pending\b|"
    r"\bsin\s+(?:promesas?|items?|gates?|pendientes?)\s+(?:abiertas?|pendientes?)\b",
    re.IGNORECASE,
)
MANUAL_PENDING_NEGATION_RE = re.compile(
    r"\b(?:no|sin)\s+queda\s+(?:nada\s+)?pendiente\b",
    re.IGNORECASE,
)


def _has_pending_release_gate(text: str) -> bool:
    for match in PENDING_RELEASE_GATE_RE.finditer(text or ""):
        snippet = match.group(0)
        if PENDING_RELEASE_NEGATED_RE.search(snippet):
            continue
        return True
    return False
MANUAL_PENDING_MARKER_RE = re.compile(
    r"\b("
    r"qued(?:a|an)\s+pendiente(?:s)?|"
    r"pasos?\s+manual(?:es)?\s+pendiente(?:s)?|"
    r"tras\s+esto|"
    r"una\s+vez\s+aprobado|"
    r"siguientes?\s+pasos?|"
    r"manual\s+steps?|"
    r"pending\s+manual\s+steps?"
    r")\b",
    re.IGNORECASE,
)
MANUAL_PENDING_ACTION_RE = re.compile(
    r"\b("
    r"stage|commit|tag|publish|deploy|"
    r"configurar|contratar|verificar|publicar|desplegar|"
    r"etiquetar|subir|firmar|notari[sz]ar"
    r")\b",
    re.IGNORECASE,
)
MANUAL_PENDING_STEP_LINE_RE = re.compile(
    r"(?im)^\s*(?:[-*+]|\d+[.)])\s*"
    r"(?:stage|commit|tag|publish|deploy|configurar|contratar|verificar|publicar|desplegar|etiquetar|subir|firmar|notari[sz]ar)\b"
)
TIME_BOUND_COMMITMENT_RE = re.compile(
    r"(?:"
    r"\b(?:revisar(?:[eé]|emos)?|comprobar(?:[eé]|emos)?|verificar(?:[eé]|emos)?|mirar(?:[eé]|emos)?|"
    r"avisar(?:[eé]|emos)?|reenviar(?:[eé]|emos)?|recordar(?:[eé]|emos)?|nudge|follow(?:\s|-)?up|"
    r"seguimiento|build|release|merge(?:ar)?|logs?)\b"
    r".{0,140}\b(?:en\s+\d+\s*(?:h|horas?|hours?|d[ií]as?|days?)|ma[nñ]ana(?:\s*/\s*pasado|\s+o\s+pasado)?|pasado\s+ma[nñ]ana|tomorrow|in\s+\d+\s*(?:h|hours?|days?))\b"
    r"|"
    r"\b(?:en\s+\d+\s*(?:h|horas?|hours?|d[ií]as?|days?)|ma[nñ]ana(?:\s*/\s*pasado|\s+o\s+pasado)?|pasado\s+ma[nñ]ana|tomorrow|in\s+\d+\s*(?:h|hours?|days?))\b"
    r".{0,140}\b(?:revisar(?:[eé]|emos)?|comprobar(?:[eé]|emos)?|verificar(?:[eé]|emos)?|mirar(?:[eé]|emos)?|"
    r"avisar(?:[eé]|emos)?|reenviar(?:[eé]|emos)?|recordar(?:[eé]|emos)?|nudge|follow(?:\s|-)?up|"
    r"seguimiento|build|release|merge(?:ar)?|logs?)\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)
OPEN_COMMITMENT_FOLLOWUP_RE = re.compile(
    r"\b("
    r"queda(?:n)?\s+pendiente(?:s)?(?:\s+de)?|"
    r"lo\s+dejo\s+(?:pendiente|como\s+seguimiento|para\s+(?:despu[eé]s|m[aá]s\s+tarde))|"
    r"lo\s+retomo\s+(?:m[aá]s\s+tarde|en\s+otra\s+sesi[oó]n)|"
    r"lo\s+vemos\s+en\s+otra\s+sesi[oó]n|"
    r"idea\s+aparcada|"
    r"bloquead[oa]\s+por\s+(?:auth|autenticaci[oó]n|seguridad|security|credenciales?|permisos?)|"
    r"seguridad\s+pendiente|"
    r"pending\s+(?:followup|follow-up|security|credentials?|auth)|"
    r"blocked\s+by\s+(?:auth|security|credentials?|permissions?)|"
    r"defer(?:red)?\s+(?:idea|followup|follow-up)|"
    r"follow(?:\s|-)?up\s+(?:later|pending|needed)"
    r")\b",
    re.IGNORECASE,
)
COMMITMENT_FOLLOWUP_DELIVERABLE_RE = re.compile(
    r"\b("
    r"implementar|crear|preparar|verificar|revisar|confirmar|resolver|enviar|"
    r"entregable|deliverable|fix|hook|script|smoke|evidencia|evidence|"
    r"implement|create|prepare|verify|review|confirm|resolve|send"
    r")\b",
    re.IGNORECASE,
)
FOLLOWUP_OR_REMINDER_REF_PATTERN = re.compile(r"\b(?:NF|R)-[A-Z0-9][A-Z0-9-]*\b", re.IGNORECASE)
IRREVERSIBLE_ACTION_RE = re.compile(
    r"\b(publish\s+stable|publicar\s+stable|promocionar\s+stable|broadcast|enviar\s+a\s+clientes|cobrar|payment|force-push|revocar)\b",
    re.IGNORECASE,
)
SPECIFIC_OK_AFTER_EVIDENCE_RE = re.compile(
    r"\b(aprobaci[oó]n\s+expl[ií]cita|ok\s+espec[ií]fico|autorizaci[oó]n\s+espec[ií]fica|specific\s+ok|explicit\s+approval)\b"
    r".{0,120}\b(evidencia|evidence|smoke|verificad[oa]|verified)\b",
    re.IGNORECASE | re.DOTALL,
)
PUBLIC_RELEASE_EVIDENCE_RE = re.compile(
    r"("
    r"screenshot|captura|"
    r"url\s+p[uú]blica|public\s+url|url-public|"
    r"https?://[^\s]+|"
    r"\bHTTP/?\d?(?:\.\d)?\s+200\b|"                   # HTTP 200, HTTP/1.1 200, HTTP/2 200
    r"\b200\s+OK\b|"                                    # 200 OK
    r"\bhttp[_\s-]?code\b[^\n]{0,40}\b200\b|"          # http_code: 200 (curl -w '%{http_code}')
    r"\bstatus(?:[_\s-]?code)?\b[^\n]{0,20}\b200\b|"   # status 200 / status_code 200
    r"\bc[oó]digo\b[^\n]{0,20}\b200\b"                 # código 200 / código de estado 200
    r")",
    re.IGNORECASE,
)
PUBLIC_RELEASE_WORK_RE = re.compile(
    r"\b(release|deploy|deployment|publish|publicar|desplegar|lanzar|stable|producci[oó]n|production)\b",
    re.IGNORECASE,
)
VISUAL_PUBLIC_EVIDENCE_RE = re.compile(
    r"\b(?:screenshot|captura|captura\s+visual|visual\s+capture)\b[^\n]{0,160}\b(?:\.png|\.jpg|\.jpeg|\.webp|playwright|browser|navegador|headed)\b",
    re.IGNORECASE,
)
END_USER_E2E_EVIDENCE_RE = re.compile(
    r"\b(?:flujo|recorrido|flow|journey|e2e|end[- ]to[- ]end)\b[^\n]{0,180}\b(?:usuario\s+final|final\s+user|end\s+user|cliente|customer)\b|\b(?:usuario\s+final|final\s+user|end\s+user|cliente|customer)\b[^\n]{0,180}\b(?:flujo|recorrido|flow|journey|e2e|end[- ]to[- ]end)\b",
    re.IGNORECASE,
)
OPERATOR_SUCCESS_CRITERION_RE = re.compile(
    r"\b(?:criterio\s+de\s+[eé]xito\s+espec[ií]fico(?:\s+del\s+operador)?|operator\s+success\s+criterion|specific\s+success\s+criterion|criterio\s+francisco|francisco\s+criterion)\b\s*[:=-]\s*\S.{20,}",
    re.IGNORECASE,
)
HIGH_STAKES_WORK_TYPES = {"release", "deploy", "deployment", "publish", "publicar", "desplegar"}
VISIBLE_RELEASE_SURFACE_PATTERNS = {
    "api": (
        re.compile(r"\bapi\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bendpoint(?:s)?\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bruta(?:s)?\b\s*[:=-]", re.IGNORECASE),
    ),
    "ui": (
        re.compile(r"\bui\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bux\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\binterfaz\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bnavegador\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bbrowser\b\s*[:=-]", re.IGNORECASE),
    ),
    "dominio_publico": (
        re.compile(r"\bdominio\s+p[uú]blico\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bpublic\s+domain\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\burl\s+p[uú]blica\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bpublic\s+url\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"https?://[^\s]+", re.IGNORECASE),
    ),
    "endpoint_vivo": (
        re.compile(r"\bendpoint\s+vivo\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\blive\s+endpoint\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\b(?:curl|httpie)\b[^\n]{0,180}\bhttps?://[^\s]+[^\n]{0,120}\b(?:HTTP\s*200|200\s+OK|status(?:_code)?\s*200|http_code\b[^\n]{0,40}\b200)\b", re.IGNORECASE),
    ),
    "revision_desplegada": (
        re.compile(r"\brevisi[oó]n\s+desplegada\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bdeployed\s+revision\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\b(?:cloud\s+run\s+revision|revision|commit|sha)\b[^\n]{0,140}\b(?:desplegad[ao]|deployed|sirve|serving|active|activa)\b", re.IGNORECASE),
    ),
    "url_antigua_emitida": (
        re.compile(r"\burl\s+antigua\s+ya\s+emitida\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\b(?:old|legacy)\s+url\s+(?:already\s+)?issued\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\b(?:enlace|url)\s+(?:antigu[oa]|legacy|old)\b[^\n]{0,160}\b(?:emitid[ao]|issued|ya\s+enviado|already\s+sent|N/?A)\b", re.IGNORECASE),
    ),
    "estado_interno_afectado": (
        re.compile(r"\bestado\s+interno\s+afectado\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\baffected\s+internal\s+state\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\b(?:bd|db|database|metafields?|shopify|queue|cola|token|estado\s+interno)\b[^\n]{0,180}\b(?:verificad[ao]|checked|ok|sin\s+pendientes|consistent|N/?A)\b", re.IGNORECASE),
    ),
    "rama_publicacion": (
        re.compile(r"\brama\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bbranch\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\borigin/(?:main|master|stable|release|gh-pages)\b", re.IGNORECASE),
        re.compile(r"\bgh-pages\b", re.IGNORECASE),
    ),
    "git_limpio": (
        re.compile(r"\bgit\s+limpio\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bclean\s+(?:git|worktree|working\s+tree)\b\s*[:=-]?", re.IGNORECASE),
        re.compile(r"\bgit\s+status\b[^\n]{0,80}\b(?:clean|limpio|sin\s+cambios|nothing\s+to\s+commit)\b", re.IGNORECASE),
    ),
    "artefactos": (
        re.compile(r"\bartefacto(?:s)?\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bartifact(?:s)?\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\b(?:dmg|exe|zip|image|imagen|cloud\s+build|github\s+release)\b", re.IGNORECASE),
    ),
    "artefacto_correcto": (
        re.compile(r"\bartefacto\s+correcto\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bcorrect\s+artifact\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\b(?:sha256|checksum|hash)\b[^\n]{0,120}\b(?:match|coincide|verificad[oa])\b", re.IGNORECASE),
        re.compile(r"\bversi[oó]n\b[^\n]{0,80}\b(?:coincide|match|verificad[oa])\b", re.IGNORECASE),
    ),
    "firma_notarizacion": (
        re.compile(r"\bfirma(?:do)?(?:\s*/\s*|\s+y\s+)notari[sz]aci[oó]n\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\b(?:signature|signed)(?:\s*/\s*|\s+and\s+)notari[sz](?:ed|ation)\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\b(?:codesign|spctl|notarytool|notarized|notarizado|signed|firmado)\b", re.IGNORECASE),
        re.compile(r"\bfirma(?:do)?\b\s*[:=-]\s*N/?A\b", re.IGNORECASE),
    ),
    "manifiestos": (
        re.compile(r"\bmanifiesto(?:s)?\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bmanifest(?:s)?\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\b(?:manifest\.json|update\.json|package\.json|composer\.json)\b", re.IGNORECASE),
    ),
    "smoke_publico": (
        re.compile(r"\bsmoke\s+p[uú]blico\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bpublic\s+smoke\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bsmoke\b[^\n]{0,120}\bhttps?://[^\s]+[^\n]{0,80}\b(?:HTTP\s*200|200\s+OK|status(?:_code)?\s*200)\b", re.IGNORECASE),
    ),
    "captura_visual_ui": (
        re.compile(r"\bcaptura\s+visual\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bvisual\s+capture\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\b(?:screenshot|captura)\b[^\n]{0,120}\b(?:\.png|\.jpg|\.jpeg|\.webp|N/?A)\b", re.IGNORECASE),
    ),
    "monitor_sin_redirects": (
        re.compile(r"\bmonitor\b[^\n]{0,120}\b(?:sin\s+redirects?|no\s+implicit\s+redirects?|without\s+implicit\s+redirects?)\b", re.IGNORECASE),
        re.compile(r"\b(?:redirects?\s+impl[ií]citos?|implicit\s+redirects?)\b[^\n]{0,80}\b(?:none|ninguno|no|0|sin)\b", re.IGNORECASE),
        re.compile(r"\bmonitor\s+sin\s+redirects?\s+impl[ií]citos\b\s*[:=-]", re.IGNORECASE),
    ),
    "prueba_viva": (
        re.compile(r"\bprueba\s+viva\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\blive\s+test\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\bsmoke\b\s*[:=-]", re.IGNORECASE),
        re.compile(r"\b(?:curl|playwright|logs?\s+vivos?|gcloud\s+logs|http\s*200|200\s+ok)\b", re.IGNORECASE),
    ),
}
VISIBLE_RELEASE_SURFACE_LABELS = {
    "api": "API",
    "ui": "UI",
    "dominio_publico": "dominio público",
    "endpoint_vivo": "endpoint vivo",
    "revision_desplegada": "revisión desplegada",
    "url_antigua_emitida": "URL antigua ya emitida",
    "estado_interno_afectado": "estado interno afectado",
    "rama_publicacion": "rama de publicación",
    "git_limpio": "git limpio",
    "artefactos": "artefactos",
    "artefacto_correcto": "artefacto correcto",
    "firma_notarizacion": "firma/notarización",
    "manifiestos": "manifiestos",
    "smoke_publico": "smoke público",
    "captura_visual_ui": "captura visual UI",
    "monitor_sin_redirects": "monitor sin redirects implícitos",
    "prueba_viva": "prueba viva",
}


def _normalize_artifact_hash(value: str) -> str:
    clean = (value or "").strip().lower()
    clean = re.sub(r"^(sha256|sha1|md5):", "", clean).strip()
    return clean


def _requires_irreversible_artifact_hash(closure_text: str, artifact_hash: str, validated_hash: str) -> tuple[bool, str]:
    if not IRREVERSIBLE_ACTION_RE.search(closure_text):
        return False, ""
    current = _normalize_artifact_hash(artifact_hash)
    validated = _normalize_artifact_hash(validated_hash)
    if not current or not validated:
        return True, "missing"
    if current != validated:
        return True, "mismatch"
    return False, ""


def _latest_cortex_evaluation_satisfies_irreversible_verify(task_id: str) -> tuple[bool, str]:
    evaluation = latest_cortex_evaluation_for_task(task_id)
    if not evaluation:
        return False, "missing"
    impact = str(evaluation.get("impact_level") or "").strip().lower()
    if impact not in {"high", "critical"}:
        return False, "impact_level"
    evidence_text = "\n".join(
        str(evaluation.get(field) or "")
        for field in (
            "context_hint",
            "recommended_reasoning",
            "heuristic_reasoning",
            "selection_reason",
            "alternatives",
            "scores",
        )
    )
    if not re.search(
        r"\b(verify|verific|evidencia|evidence|validaci[oó]n\s+humana|human\s+validation|artifact|artefacto|hash|sha256)\b",
        evidence_text,
        re.IGNORECASE,
    ):
        return False, "verify_evidence"
    return True, ""


def _is_high_stakes_public_work(task: dict, work_type: str, stakes: str, closure_text: str) -> bool:
    high_override = _parse_high_stakes_override(stakes or "")
    high_stakes = bool(task.get("response_high_stakes")) if high_override is None else high_override
    if not high_stakes:
        return False
    clean_work_type = (work_type or "").strip().lower()
    if clean_work_type in HIGH_STAKES_WORK_TYPES:
        return True
    return bool(PUBLIC_RELEASE_WORK_RE.search(closure_text or ""))


def _has_public_release_evidence(evidence: str) -> bool:
    if _is_trivial_evidence(evidence)[0]:
        return False
    return bool(PUBLIC_RELEASE_EVIDENCE_RE.search(evidence or ""))


def _missing_ux_first_release_gate_items(evidence: str) -> list[str]:
    text = evidence or ""
    missing: list[str] = []
    if not VISUAL_PUBLIC_EVIDENCE_RE.search(text):
        missing.append("captura o evidencia visual pública")
    if not END_USER_E2E_EVIDENCE_RE.search(text):
        missing.append("flujo end-to-end como usuario final")
    if not OPERATOR_SUCCESS_CRITERION_RE.search(text):
        missing.append("criterio de éxito específico del operador")
    return missing


def _missing_visible_release_surfaces(evidence: str) -> list[str]:
    text = evidence or ""
    missing: list[str] = []
    for key, patterns in VISIBLE_RELEASE_SURFACE_PATTERNS.items():
        if not any(pattern.search(text) for pattern in patterns):
            missing.append(VISIBLE_RELEASE_SURFACE_LABELS[key])
    return missing


VERIFIED_AGAINST_REAL_SCENARIO_RE = re.compile(
    r"\b(?:scenario|escenario|case|caso|flow|flujo|symptom|s[ií]ntoma|reproduc(?:ed|ido|ida|ir)|repro)\b",
    re.IGNORECASE,
)
VERIFIED_AGAINST_REAL_DATA_RE = re.compile(
    r"\b(?:real(?:es)?|live|producci[oó]n|production|cliente|customer|pedido|order|booking|reserva|bd|db|cloud sql|shopify|vapi)\b",
    re.IGNORECASE,
)
VERIFIED_AGAINST_REAL_HYPOTHESES_RE = re.compile(
    r"\b(?:hip[oó]tesis|hypothes(?:is|es)|not reproduced|no reproducid(?:a|as|o|os)|descartad(?:a|as|o|os)|ruled out|no confirmad(?:a|as|o|os))\b",
    re.IGNORECASE,
)
PARTIAL_VERIFICATION_ACK_TOKEN = "partial_verification_acknowledged"
LIVE_ACTION_WORK_TYPES = {
    "apply",
    "aplicar",
    "campaign",
    "campaña",
    "campana",
    "deploy",
    "deployment",
    "desplegar",
    "despliegue",
    "publish",
    "publicar",
}
LIVE_ACTION_FINAL_URL_RE = re.compile(
    r"(?=.*\b(?:final\s+url|final\s+urls|url(?:s)?\s+final(?:es)?|landing(?:s)?)\b)"
    r"(?=.*\b(?:http\s*(?:status\s*)?(?:=|:|->)?\s*200|200\s+ok|curl\s+-I|HEAD)\b)",
    re.IGNORECASE | re.DOTALL,
)
LIVE_ACTION_GAQL_RE = re.compile(
    r"(?=.*\bgaql\b)"
    r"(?=.*\b(?:post[-\s]?mutaci[oó]n|post[-\s]?mutation|recuento|count|created|mutated)\b)",
    re.IGNORECASE | re.DOTALL,
)
LIVE_ACTION_NPM_RE = re.compile(
    r"(?=.*\bnpm\s+view\b)(?=.*\b(?:latest|dist-tags?|==|=)\b)",
    re.IGNORECASE | re.DOTALL,
)
LIVE_ACTION_OS_LOGIN_RE = re.compile(
    r"(?=.*\b(?:login|inicio\s+de\s+sesi[oó]n)\b)"
    r"(?=.*\b(?:real|windows|macos|linux|os|so|vm)\b)",
    re.IGNORECASE | re.DOTALL,
)


def _requires_verified_against_real_checklist(
    task: dict,
    original_outcome: str,
    clean_outcome: str,
    work_type: str,
    stakes: str,
    closure_text: str,
) -> bool:
    if str(task.get("task_type") or "").strip() != "execute":
        return False
    clean_original = (original_outcome or "").strip().lower()
    explicit_publish_outcome = clean_original in {"published", "deployed"}
    if clean_outcome not in {"done", "partial"} and not explicit_publish_outcome:
        return False
    return explicit_publish_outcome or _is_high_stakes_public_work(task, work_type, stakes, closure_text)


def _missing_verified_against_real_items(verification_evidence) -> list[str]:
    items = _parse_list(verification_evidence)
    joined = "\n".join(items)
    missing: list[str] = []
    if not items or not VERIFIED_AGAINST_REAL_SCENARIO_RE.search(joined):
        missing.append("escenario reproducido")
    if not items or not VERIFIED_AGAINST_REAL_DATA_RE.search(joined):
        missing.append("datos reales usados")
    if not items or not VERIFIED_AGAINST_REAL_HYPOTHESES_RE.search(joined):
        missing.append("hipótesis no reproducidas")
    return missing


def _requires_live_action_close_gate(task: dict, work_type: str, closure_text: str) -> bool:
    clean_work_type = (work_type or "").strip().lower()
    if clean_work_type in LIVE_ACTION_WORK_TYPES:
        return True
    return False


def _missing_live_action_evidence(task: dict, work_type: str, closure_text: str, evidence: str) -> list[str]:
    if not _requires_live_action_close_gate(task, work_type, closure_text):
        return []
    evidence_text = evidence or ""
    scope_text = " ".join(
        str(part or "")
        for part in (
            work_type,
            task.get("goal"),
            task.get("area"),
            task.get("project_hint"),
            task.get("verification_step"),
            closure_text,
        )
    ).lower()
    missing: list[str] = []
    is_campaign = bool(re.search(r"\b(?:campaign|campa(?:ñ|n)a|google\s+ads|gaql|rsa|final\s+url)\b", scope_text))
    is_package_publish = bool(re.search(r"\b(?:npm|package|paquete)\b", scope_text))
    is_os_target = bool(re.search(r"\b(?:windows|macos|linux|os|so|vm|desktop)\b", scope_text))

    if is_campaign:
        if not LIVE_ACTION_FINAL_URL_RE.search(evidence_text):
            missing.append("HTTP 200 de cada Final URL")
        if not LIVE_ACTION_GAQL_RE.search(evidence_text):
            missing.append("recuento GAQL post-mutación")
    elif not (
        LIVE_ACTION_FINAL_URL_RE.search(evidence_text)
        or LIVE_ACTION_GAQL_RE.search(evidence_text)
        or LIVE_ACTION_NPM_RE.search(evidence_text)
        or LIVE_ACTION_OS_LOGIN_RE.search(evidence_text)
    ):
        missing.append("evidencia live concreta")

    if is_package_publish and not LIVE_ACTION_NPM_RE.search(evidence_text):
        missing.append("npm view <pkg>@version == latest")
    if is_os_target and not LIVE_ACTION_OS_LOGIN_RE.search(evidence_text):
        missing.append("login real en el SO objetivo")
    return missing


def _append_note(base: str, note: str) -> str:
    clean_note = (note or "").strip()
    if not clean_note:
        return (base or "").strip()
    clean_base = (base or "").strip()
    return f"{clean_base}\n{clean_note}".strip() if clean_base else clean_note


def _active_followup_snapshot(limit: int = 5) -> list[dict]:
    try:
        followups = get_followups("active")
    except Exception:
        return []
    snapshot: list[dict] = []
    for item in followups[: max(1, limit)]:
        snapshot.append(
            {
                "id": item.get("id", ""),
                "status": item.get("status", ""),
                "date": item.get("date", ""),
                "description": str(item.get("description", ""))[:180],
            }
        )
    return snapshot


def _closure_claim_text(*parts: object) -> str:
    return "\n".join(str(part or "") for part in parts if str(part or "").strip())


def _manual_pending_close_requires_followup(text: str) -> bool:
    """Detect visible close text that leaves manual steps pending."""
    clean = MANUAL_PENDING_NEGATION_RE.sub("", str(text or ""))
    if not clean.strip():
        return False
    if MANUAL_PENDING_STEP_LINE_RE.search(clean):
        return True
    return bool(MANUAL_PENDING_MARKER_RE.search(clean) and MANUAL_PENDING_ACTION_RE.search(clean))


def _has_followup_for_manual_pending(created_followup_id: str, *parts: object) -> bool:
    if str(created_followup_id or "").strip():
        return True
    for part in parts:
        for followup_ref in FOLLOWUP_REF_PATTERN.findall(str(part or "")):
            try:
                row = get_followup(followup_ref.upper())
            except Exception:
                row = None
            if row and str(row.get("status") or "").upper() != "DELETED":
                return True
    return False


def _has_time_bound_commitment(text: str) -> bool:
    return bool(TIME_BOUND_COMMITMENT_RE.search(str(text or "")))


def _has_open_commitment_without_followup_signal(text: str) -> bool:
    return bool(OPEN_COMMITMENT_FOLLOWUP_RE.search(str(text or "")))


def _item_has_active_date(ref: str) -> bool:
    clean_ref = str(ref or "").strip().upper()
    if not clean_ref:
        return False
    try:
        row = get_followup(clean_ref) if clean_ref.startswith("NF-") else get_reminder(clean_ref)
    except Exception:
        row = None
    if not row:
        return False
    if str(row.get("status") or "").upper() == "DELETED":
        return False
    return bool(str(row.get("date") or "").strip())


def _followup_has_commitment_payload(ref: str) -> bool:
    clean_ref = str(ref or "").strip().upper()
    if not clean_ref.startswith("NF-"):
        return False
    try:
        row = get_followup(clean_ref)
    except Exception:
        row = None
    if not row:
        return False
    if str(row.get("status") or "").upper() == "DELETED":
        return False
    description = str(row.get("description") or "").strip()
    verification = str(row.get("verification") or "").strip()
    date = str(row.get("date") or "").strip()
    if not date or not description or not verification:
        return False
    return bool(COMMITMENT_FOLLOWUP_DELIVERABLE_RE.search(description))


def _has_complete_followup_for_open_commitment(
    created_followup_id: str,
    provided_followup_id: str,
    *parts: object,
) -> bool:
    if _followup_has_commitment_payload(created_followup_id):
        return True
    if _followup_has_commitment_payload(provided_followup_id):
        return True
    for part in parts:
        for ref in FOLLOWUP_REF_PATTERN.findall(str(part or "")):
            if _followup_has_commitment_payload(ref):
                return True
    return False


def _has_dated_followup_for_time_bound_commitment(
    created_followup_id: str,
    provided_followup_id: str,
    *parts: object,
) -> bool:
    if _item_has_active_date(created_followup_id):
        return True
    if _item_has_active_date(provided_followup_id):
        return True
    for part in parts:
        for ref in FOLLOWUP_OR_REMINDER_REF_PATTERN.findall(str(part or "")):
            if _item_has_active_date(ref):
                return True
    return False


def handle_confidence_check(
    goal: str,
    task_type: str = "answer",
    area: str = "",
    context_hint: str = "",
    constraints: str = "[]",
    evidence_refs: str = "[]",
    unknowns: str = "[]",
    verification_step: str = "",
    stakes: str = "",
    sid: str = "",
) -> str:
    """Return the metacognitive response mode: answer, verify, ask, or defer."""
    clean_goal = (goal or "").strip()
    if not clean_goal:
        return json.dumps({"ok": False, "error": "goal is required"}, ensure_ascii=False, indent=2)
    try:
        clean_type = validate_task_type(task_type)
    except ValueError as exc:
        return json.dumps(
            {
                "ok": False,
                "error": str(exc),
                "valid_task_types": sorted(VALID_TASK_TYPES),
            },
            ensure_ascii=False,
            indent=2,
        )
    result = evaluate_response_confidence(
        goal=clean_goal,
        task_type=clean_type,
        area=(area or "").strip(),
        context_hint=(context_hint or "").strip(),
        constraints=_parse_list(constraints),
        evidence_refs=_parse_list(evidence_refs),
        unknowns=_parse_list(unknowns),
        verification_step=(verification_step or "").strip(),
        stakes=(stakes or "").strip(),
    )
    # Persist the check so the G1 answer-contract gate can detect fulfillment of
    # verify/ask/defer contracts (this table was previously never written, so
    # verify contracts were structurally unfulfillable). Best-effort: a failure
    # here must never break the metacognitive answer — g1 simply re-nudges, a
    # visible signal rather than a silent corruption.
    try:
        import hashlib
        from db import get_db
        from plugins.guard import _resolve_active_sid
        conn = get_db()
        resolved_sid = (sid or "").strip() or _resolve_active_sid(conn)
        if resolved_sid:
            conn.execute(
                """INSERT INTO confidence_checks
                   (session_id, task_id, goal_hash, task_type, area,
                    response_mode, confidence, high_stakes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    resolved_sid,
                    "",
                    hashlib.sha256(clean_goal.encode("utf-8")).hexdigest()[:16],
                    clean_type,
                    (area or "").strip(),
                    str(result.get("mode") or ""),
                    int(result.get("confidence") or 0),
                    1 if result.get("high_stakes") else 0,
                ),
            )
            conn.commit()
    except Exception:
        pass
    return json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2)


def handle_task_open(
    sid: str,
    goal: str = "",
    task_type: str = "answer",
    area: str = "",
    files: str = "",
    project_hint: str = "",
    plan: str = "[]",
    known_facts: str = "[]",
    unknowns: str = "[]",
    constraints: str = "[]",
    evidence_refs: str = "[]",
    verification_step: str = "",
    stakes: str = "",
    context_hint: str = "",
    description: str = "",
    ack_rules: str = "",
) -> str:
    """Open a protocol task with heartbeat, guard, rules, and Cortex already captured.

    Use this as the default entry point for any non-trivial work. For edit/execute/delegate
    tasks it becomes the contract that later must be closed with `nexo_task_close`.
    """
    clean_goal = (goal or description or "").strip()
    if not sid.strip():
        return json.dumps({"ok": False, "error": "sid is required"}, ensure_ascii=False, indent=2)
    if not clean_goal:
        return json.dumps({"ok": False, "error": "goal is required"}, ensure_ascii=False, indent=2)

    try:
        clean_type = validate_task_type(TASK_TYPE_ALIASES.get((task_type or "").strip().lower(), task_type))
    except ValueError as exc:
        return json.dumps(
            {
                "ok": False,
                "error": str(exc),
                "valid_task_types": sorted(VALID_TASK_TYPES),
            },
            ensure_ascii=False,
            indent=2,
        )
    files_list = _parse_list(files)
    protocol_strictness = get_protocol_strictness()
    if protocol_strictness in {"strict", "learning"} and clean_type == "edit" and not files_list:
        note = (
            "Strict protocol mode requires explicit `files` for edit tasks."
            if protocol_strictness == "strict"
            else "Learning mode requires explicit `files` on edit tasks so NEXO can match the write against the open protocol task."
        )
        return json.dumps(
            {"ok": False, "error": note, "protocol_strictness": protocol_strictness},
            ensure_ascii=False,
            indent=2,
        )
    plan_items = _parse_list(plan)
    if not plan_items:
        plan_items = _extract_plan_from_goal(clean_goal)

    state = {
        "goal": clean_goal,
        "task_type": clean_type,
        "plan": plan_items,
        "known_facts": _parse_list(known_facts),
        "unknowns": _parse_list(unknowns),
        "constraints": _parse_list(constraints),
        "evidence_refs": _parse_list(evidence_refs),
        "verification_step": (verification_step or "").strip(),
    }
    response_contract = evaluate_response_confidence(
        goal=clean_goal,
        task_type=clean_type,
        area=area.strip(),
        context_hint=context_hint.strip(),
        constraints=state["constraints"],
        evidence_refs=state["evidence_refs"],
        unknowns=state["unknowns"],
        verification_step=state["verification_step"],
        stakes=stakes,
    )
    operational_state = build_operational_state_policy(
        area=area.strip() or "general",
        task_type=clean_type,
        source_refs=[
            ref
            for ref in state["evidence_refs"]
            if isinstance(ref, str) and ref.split(":", 1)[0] in OPERATIONAL_SOURCE_PREFIXES
        ],
        response_contract=response_contract,
        current_instruction=context_hint.strip() or clean_goal,
        auto_collect=True,
        persist=True,
    )
    recent_bundle = build_pre_action_context(
        query=" | ".join(part for part in [clean_goal, context_hint.strip()] if part),
        session_id=sid.strip(),
        hours=24,
        limit=4,
    )
    area_context = _build_area_context(area.strip()) if area.strip() else {"has_context": False}
    heartbeat_result = handle_heartbeat(sid, clean_goal[:120], context_hint=context_hint[:500])
    attention = _attention_snapshot(sid.strip())
    anticipatory_warnings = _preview_prospective_triggers(clean_goal, context_hint.strip(), files_list)
    preventive_followup = None

    guard_summary = ""
    guard_has_blocking = False
    opened_with_guard = False
    debts_created: list[dict] = []
    if clean_type in ACTION_TASKS and (files_list or area.strip()):
        opened_with_guard = True
        guard_summary = handle_guard_check(
            files=",".join(files_list),
            area=area.strip(),
            project_hint=project_hint.strip(),
        )
        guard_has_blocking = (
            "[BLOCKING]" in guard_summary
            or "WARNINGS — resolve before editing" in guard_summary
            or "BLOCKING RULES" in guard_summary
        )

    cortex = evaluate_cortex_state(state)
    decision_support = {
        "required": _decision_support_required(
            task_type=clean_type,
            high_stakes=response_contract["high_stakes"],
        ),
        "tool": "nexo_cortex_decide",
        "reason": (
            "High-stakes action task detected. Rank at least 2 alternatives before acting."
            if clean_type in ACTION_TASKS and response_contract["high_stakes"]
            else "Alternative ranking not required for this task."
        ),
    }
    must_verify = clean_type in ACTION_TASKS or response_contract["mode"] == "verify"
    if clean_type in ACTION_TASKS and operational_state.get("verification_requirement") in {"single", "double", "external", "release_gate"}:
        must_verify = True
    must_change_log = clean_type in {"edit", "execute"} and bool(files_list)
    must_learning_if_corrected = True
    must_write_diary_on_close = clean_type in ACTION_TASKS
    p0_repeated_symptom = _is_repeated_symptom_p0_task(
        {
            "goal": clean_goal,
            "area": area.strip(),
            "project_hint": project_hint.strip(),
            "context_hint": context_hint.strip(),
            "known_facts": json.dumps(state["known_facts"], ensure_ascii=False),
            "constraints": json.dumps(state["constraints"], ensure_ascii=False),
            "evidence_refs": json.dumps(state["evidence_refs"], ensure_ascii=False),
            "verification_step": state["verification_step"],
        },
        "",
    )

    task = create_protocol_task(
        sid,
        clean_goal,
        task_type=clean_type,
        area=area.strip(),
        project_hint=project_hint.strip(),
        context_hint=context_hint.strip(),
        files=files_list,
        plan=state["plan"],
        known_facts=state["known_facts"],
        unknowns=state["unknowns"],
        constraints=state["constraints"],
        evidence_refs=state["evidence_refs"],
        verification_step=state["verification_step"],
        cortex_mode=cortex["mode"],
        cortex_check_id=cortex["check_id"],
        cortex_blocked_reason=cortex.get("blocked_reason") or "",
        cortex_warnings=cortex.get("warnings") or [],
        cortex_rules=cortex.get("injected_rules") or [],
        opened_with_guard=opened_with_guard,
        opened_with_rules=True,
        guard_has_blocking=guard_has_blocking,
        guard_summary=guard_summary,
        must_verify=must_verify,
        must_change_log=must_change_log,
        must_learning_if_corrected=must_learning_if_corrected,
        must_write_diary_on_close=must_write_diary_on_close,
        response_mode=response_contract["mode"],
        response_confidence=response_contract["confidence"],
        response_reasons=response_contract["reasons"],
        response_high_stakes=response_contract["high_stakes"],
    )
    protocol_context_key = f"protocol_task:{task['task_id']}"
    capture_context_event(
        event_type="protocol_task_opened",
        title=clean_goal[:160],
        summary=(context_hint or clean_goal)[:600],
        body="\n".join(state["plan"][:5])[:1600] if state["plan"] else "",
        context_key=protocol_context_key,
        context_title=clean_goal[:160],
        context_summary=(context_hint or clean_goal)[:600],
        context_type="protocol_task",
        state="active",
        owner="nexo",
        actor=sid,
        source_type="protocol_task",
        source_id=task["task_id"],
        session_id=sid,
        metadata={
            "task_type": clean_type,
            "area": area.strip(),
            "files": files_list[:8],
        },
        ttl_hours=24,
    )
    blocking_rule_ids = _extract_guard_blocking_ids(guard_summary) if guard_has_blocking else []
    if not guard_has_blocking and clean_type in ACTION_TASKS and (anticipatory_warnings or attention["status"] in {"split", "overloaded"}):
        preventive_followup = _create_preventive_followup(
            clean_goal,
            attention=attention,
            warnings=anticipatory_warnings,
        )

    if guard_has_blocking:
        next_action = "Resolve the blocking guard warnings before editing."
    elif response_contract["mode"] == "defer":
        next_action = response_contract["next_action"]
    elif response_contract["mode"] == "ask" and clean_type in RESPONSE_TASKS:
        next_action = response_contract["next_action"]
    elif response_contract["mode"] == "verify" and clean_type in RESPONSE_TASKS:
        next_action = response_contract["next_action"]
    elif attention["status"] == "overloaded":
        next_action = attention["recommended_action"]
    elif anticipatory_warnings:
        next_action = "Review the anticipatory warnings before proceeding."
    elif decision_support["required"]:
        next_action = "Generate 2-3 concrete alternatives and run nexo_cortex_decide before acting."
    elif operational_state.get("autonomy_limit") == "defer":
        next_action = "Defer until the operational evidence is sufficient."
    elif operational_state.get("autonomy_limit") == "ask":
        next_action = "Ask for the missing operational input before acting."
    elif operational_state.get("autonomy_limit") == "propose":
        next_action = operational_state.get("visible_guidance") or "Propose the plan before acting."
    elif cortex["mode"] == "ask":
        next_action = "Ask for the missing information before acting."
    elif cortex["mode"] == "propose":
        next_action = "Propose the plan or verification path before acting."
    else:
        next_action = "Proceed with the task and close it with nexo_task_close before claiming completion."

    if guard_has_blocking and isinstance(response_contract, dict):
        response_contract = dict(response_contract)
        response_contract["next_action"] = next_action

    recent_excerpt = format_pre_action_context_bundle(recent_bundle, compact=True) if recent_bundle.get("has_matches") else ""
    if append_local_context_evidence is not None and _task_open_local_context_enabled():
        recent_excerpt = append_local_context_evidence(
            recent_excerpt,
            " | ".join(part for part in [clean_goal, context_hint.strip()] if part),
            limit=4,
        )

    response = {
        "ok": True,
        "task_id": task["task_id"],
        "session_id": sid,
        "goal": clean_goal,
        "task_type": clean_type,
        "protocol_strictness": protocol_strictness,
        "mode": cortex["mode"],
        "check_id": cortex["check_id"],
        "blocked_reason": cortex.get("blocked_reason"),
        "warnings": cortex.get("warnings") or [],
        "applicable_rules": cortex.get("injected_rules") or [],
        "guard": {
            "ran": opened_with_guard,
            "has_blocking": guard_has_blocking,
            "blocking_rule_ids": blocking_rule_ids,
            "summary_excerpt": _guard_excerpt(guard_summary),
        },
        "attention": attention,
        "anticipation": {
            "warning_count": len(anticipatory_warnings),
            "warnings": anticipatory_warnings,
            "recommended_action": (
                "Review these anticipatory warnings before proceeding."
                if anticipatory_warnings
                else "No anticipatory warnings."
            ),
        },
        "response_contract": response_contract,
        "operational_state": {
            "policy_uid": operational_state.get("policy_uid", ""),
            "policy_version": operational_state.get("policy_version", ""),
            "area_key": operational_state.get("area_key", ""),
            "scope_key": operational_state.get("scope_key", ""),
            "caution_level": operational_state.get("caution_level", ""),
            "communication_mode": operational_state.get("communication_mode", ""),
            "detail_mode": operational_state.get("detail_mode", ""),
            "verification_requirement": operational_state.get("verification_requirement", ""),
            "autonomy_limit": operational_state.get("autonomy_limit", ""),
            "area_risk": operational_state.get("area_risk", ""),
            "reason_codes": operational_state.get("reason_codes", []),
            "source_refs": operational_state.get("source_refs", []),
            "visible_guidance": operational_state.get("visible_guidance", ""),
        },
        "decision_support": decision_support,
        "recent_context": {
            "has_matches": bool(recent_bundle.get("has_matches") or recent_excerpt.strip()),
            "excerpt": recent_excerpt,
        },
        "area_context": area_context if area_context.get("has_context") else None,
        "contract": {
            "must_verify": must_verify,
            "must_change_log": must_change_log,
            "must_learning_if_corrected": must_learning_if_corrected,
            "must_write_diary_on_close": must_write_diary_on_close,
            "protocol_strictness": protocol_strictness,
            "priority": "P0" if p0_repeated_symptom else "",
            "repeated_symptom_p0": p0_repeated_symptom,
        },
        "session_touch": heartbeat_result.splitlines()[0] if heartbeat_result else "",
        "open_debts": debts_created,
        "preventive_followup": preventive_followup,
        "next_action": next_action,
    }

    # G7 (Francisco 2026-04-22): allow inline acknowledgement of blocking
    # guard rules so the operator does not have to chain a separate
    # `nexo_task_acknowledge_guard` call. ``ack_rules`` accepts any of:
    #   "#95,#156"   "95,156"   "95 156"   "[95, 156]"
    # and delegates to ``handle_task_acknowledge_guard`` which already
    # validates that every blocking rule is covered.
    if ack_rules and isinstance(ack_rules, str) and ack_rules.strip():
        raw = ack_rules.replace("#", "").strip()
        _resolved_task_id = str(response.get("task_id") or "").strip()
        _has_blocking = bool(
            (response.get("guard") or {}).get("has_blocking")
        )
        if _has_blocking and _resolved_task_id:
            ack_result_raw = handle_task_acknowledge_guard(
                sid=sid,
                task_id=_resolved_task_id,
                learning_ids=raw,
            )
            try:
                ack_payload = json.loads(ack_result_raw)
            except Exception:
                ack_payload = {"ok": False, "error": "ack_guard_parse_failed"}
            response["ack_guard"] = ack_payload
            if ack_payload.get("ok"):
                # Refresh the blocking flag + next_action so the caller
                # sees the post-ack state instead of the stale pre-ack one.
                response["guard"] = response.get("guard") or {}
                response["guard"]["acknowledged_inline"] = True
                response["next_action"] = (
                    "Blocking guard rules acknowledged inline via task_open."
                )
        else:
            response["ack_guard"] = {
                "ok": False,
                "skipped": True,
                "reason": (
                    "No blocking guard rules on this task — ack_rules had nothing to acknowledge."
                ),
            }
    return json.dumps(response, ensure_ascii=False, indent=2)


def handle_task_close(
    sid: str,
    task_id: str,
    outcome: str = "",
    evidence: str = "",
    files_changed: str = "",
    correction_happened: bool = False,
    change_summary: str = "",
    change_why: str = "",
    change_risks: str = "",
    change_verify: str = "",
    triggered_by: str = "",
    followup_needed: bool = False,
    followup_id: str = "",
    followup_description: str = "",
    followup_date: str = "",
    followup_verification: str = "",
    followup_reasoning: str = "",
    learning_category: str = "",
    learning_title: str = "",
    learning_content: str = "",
    learning_reasoning: str = "",
    outcome_notes: str = "",
    result: str = "",
    summary: str = "",
    verification: str = "",
    evidence_refs: str = "",
    work_type: str = "",
    stakes: str = "",
    artifact_hash: str = "",
    last_human_validation_of_artifact_hash: str = "",
    verification_evidence: str = "",
    partial_verification_acknowledged: bool = False,
    partial_verification_reason: str = "",
) -> str:
    """Close a protocol task and automatically record the required discipline artifacts."""
    task = get_protocol_task(task_id.strip())
    if not task:
        return json.dumps({"ok": False, "error": f"Unknown task_id: {task_id}"}, ensure_ascii=False, indent=2)
    if sid.strip() and task.get("session_id") and task["session_id"] != sid.strip():
        return json.dumps(
            {"ok": False, "error": f"Task {task_id} belongs to {task['session_id']}, not {sid}"},
            ensure_ascii=False,
            indent=2,
        )

    outcome_candidate = (outcome or result or "").strip()
    normalized_outcome = CLOSE_OUTCOME_ALIASES.get(outcome_candidate.lower(), outcome_candidate)
    try:
        clean_outcome = validate_close_outcome(normalized_outcome)
    except ValueError as exc:
        return json.dumps(
            {
                "ok": False,
                "error": str(exc),
                "task_id": task_id,
                "valid_outcomes": sorted(VALID_CLOSE_OUTCOMES),
            },
            ensure_ascii=False,
            indent=2,
        )
    clean_change_summary = (change_summary or summary or "").strip()
    clean_change_verify = (change_verify or verification or "").strip()
    clean_evidence = (evidence or clean_change_summary or "").strip()
    extra_refs = _parse_list(evidence_refs)
    if extra_refs:
        refs_line = "Evidence refs: " + ", ".join(extra_refs)
        clean_evidence = f"{clean_evidence}\n{refs_line}".strip() if clean_evidence else refs_line
    all_evidence_refs = [*_parse_list(task.get("evidence_refs") or "[]"), *extra_refs]
    files_changed_list = _parse_list(files_changed)
    planned_files = _parse_list(task.get("files") or "[]")
    effective_files = files_changed_list or planned_files
    correction = _parse_bool(correction_happened)
    followup_required = _parse_bool(followup_needed)

    change_log_id = None
    learning_id = None
    created_followup_id = ""
    debts_created: list[dict] = []
    requires_decision_support = _decision_support_required(
        task_type=task.get("task_type", ""),
        high_stakes=bool(task.get("response_high_stakes")),
    )
    closure_text = _closure_claim_text(
        task.get("goal", ""),
        task.get("context_hint", ""),
        clean_evidence,
        clean_change_summary,
        clean_change_verify,
        outcome_notes,
        result,
        summary,
        verification,
    )
    close_payload_text = _closure_claim_text(
        clean_evidence,
        clean_change_summary,
        clean_change_verify,
        outcome_notes,
        result,
        summary,
        verification,
        change_why,
    )

    live_action_evidence = "\n".join(
        part
        for part in (
            clean_evidence,
            clean_change_verify,
            outcome_notes,
            clean_change_summary,
            verification,
        )
        if part
    )
    missing_live_action = (
        _missing_live_action_evidence(task, work_type, closure_text, live_action_evidence)
        if clean_outcome == "done"
        else []
    )
    if missing_live_action:
        pending_note = (
            "pending-verification: cierre degradado automáticamente a partial porque falta "
            f"{', '.join(missing_live_action)}."
        )
        clean_outcome = "partial"
        clean_evidence = _append_note(clean_evidence, pending_note)
        outcome_notes = _append_note(outcome_notes, pending_note)
        followup_required = True
        followup_description = (
            followup_description
            or f"Verificar evidencia live pendiente para {task_id}: {task.get('goal', '')}"
        )
        followup_verification = (
            followup_verification
            or f"Aportar y registrar evidencia live: {', '.join(missing_live_action)}."
        )
        followup_reasoning = (
            followup_reasoning
            or "Creado automáticamente porque task_close no tenía la evidencia live mínima para outcome=done."
        )
        partial_verification_acknowledged = True
        partial_verification_reason = partial_verification_reason or pending_note

    if clean_outcome == "done":
        open_task_debts = list_protocol_debts(status="open", task_id=task_id, limit=5)
        active_followups = _active_followup_snapshot()
        if TOTAL_CLOSURE_RE.search(closure_text) and (active_followups or open_task_debts):
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "total_closure_with_open_work",
                severity="error",
                evidence=(
                    "Task close used total-closure language while open work still exists. "
                    f"open_followups={len(active_followups)} open_task_debts={len(open_task_debts)} "
                    f"claim={closure_text[:240]!r}"
                ),
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close as total/no-debt while followups or task debt are still open.",
                    "hint": "Say what was executed in this task and distinguish remaining open followups/debt, then retry.",
                    "task_id": task_id,
                    "blocked_by": "total_closure_open_work_gate",
                    "debt_id": debt.get("id"),
                    "debt_type": "total_closure_with_open_work",
                    "open_followups": active_followups,
                    "open_task_debts": open_task_debts,
                },
                ensure_ascii=False,
                indent=2,
            )
        if _has_pending_release_gate(closure_text):
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "release_gate_pending_at_close",
                severity="error",
                evidence=f"Task close attempted done while smoke/tags/merge/release pending language was present: {closure_text[:240]!r}",
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close as done while release/smoke/tag/merge work is described as pending.",
                    "hint": "Close as partial or provide evidence that the named pending gate is now verified.",
                    "task_id": task_id,
                    "blocked_by": "release_gate_pending_at_close",
                    "debt_id": debt.get("id"),
                    "debt_type": "release_gate_pending_at_close",
                },
                ensure_ascii=False,
                indent=2,
            )
        is_desktop_release_close = _is_desktop_release_task(task, closure_text)
        preclose_scope_text = " ".join(
            str(part or "")
            for part in (
                task.get("goal"),
                task.get("area"),
                task.get("project_hint"),
                task.get("context_hint"),
                task.get("known_facts"),
                task.get("verification_step"),
                clean_change_summary,
                clean_change_verify,
                outcome_notes,
                result,
                summary,
                verification,
            )
        )
        preclose_missing = [] if is_desktop_release_close else _missing_preclose_variant_gate_evidence(
            preclose_scope_text,
            close_payload_text,
        )
        if preclose_missing:
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "preclose_variant_matrix_missing",
                severity="error",
                evidence=(
                    "Task close used ready/verified/prepared language without required pre-close matrix evidence. "
                    f"Missing: {', '.join(preclose_missing)}. Claim: {closure_text[:300]!r}"
                ),
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close as verified/prepared/ready without the required public URL, variant, or real-action evidence.",
                    "hint": (
                        "Attach HEAD/HTTP 200 checks for public URLs, one executed case per declared variant, "
                        "and explicit authorization for real sends/payments when applicable."
                    ),
                    "task_id": task_id,
                    "blocked_by": "preclose_variant_matrix",
                    "missing_evidence": preclose_missing,
                    "debt_id": debt.get("id"),
                    "debt_type": "preclose_variant_matrix_missing",
                },
                ensure_ascii=False,
                indent=2,
            )
        if IRREVERSIBLE_ACTION_RE.search(closure_text) and not SPECIFIC_OK_AFTER_EVIDENCE_RE.search(closure_text):
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "irreversible_action_missing_specific_ok",
                severity="error",
                evidence=f"Irreversible action close lacks specific post-evidence approval language: {closure_text[:240]!r}",
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close irreversible publish/broadcast/payment action without a specific approval after evidence.",
                    "hint": "Record the explicit approval tied to the verified evidence, not a prior generic OK.",
                    "task_id": task_id,
                    "blocked_by": "irreversible_specific_ok_gate",
                    "debt_id": debt.get("id"),
                    "debt_type": "irreversible_action_missing_specific_ok",
                },
                ensure_ascii=False,
                indent=2,
            )
        cortex_ok, cortex_reason = _latest_cortex_evaluation_satisfies_irreversible_verify(task_id)
        if IRREVERSIBLE_ACTION_RE.search(closure_text) and not cortex_ok:
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "irreversible_action_missing_cortex_decide",
                severity="error",
                evidence=(
                    "Irreversible action close lacks a persisted nexo_cortex_decide verification "
                    f"for the published artifact. reason={cortex_reason} claim={closure_text[:240]!r}"
                ),
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close irreversible publish/broadcast/payment action without nexo_cortex_decide verification for the specific artifact.",
                    "hint": (
                        "Run `nexo_cortex_decide(...)` with alternatives and evidence tied to the operator-validated "
                        "artifact/version, then retry with matching artifact hashes."
                    ),
                    "task_id": task_id,
                    "blocked_by": "irreversible_cortex_decide_gate",
                    "debt_id": debt.get("id"),
                    "debt_type": "irreversible_action_missing_cortex_decide",
                    "cortex_status": cortex_reason,
                    "response_mode": "verify",
                },
                ensure_ascii=False,
                indent=2,
            )
        hash_blocked, hash_reason = _requires_irreversible_artifact_hash(
            closure_text,
            artifact_hash,
            last_human_validation_of_artifact_hash,
        )
        if hash_blocked:
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "irreversible_artifact_hash_unverified",
                severity="error",
                evidence=(
                    "Irreversible action close lacks matching human-validated artifact hash. "
                    f"reason={hash_reason} artifact_hash={_normalize_artifact_hash(artifact_hash)[:16]} "
                    f"validated_hash={_normalize_artifact_hash(last_human_validation_of_artifact_hash)[:16]}"
                ),
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close irreversible publish/broadcast/payment action without a matching human-validated artifact hash.",
                    "hint": (
                        "Run the release decision in verify mode and retry with "
                        "`artifact_hash` plus `last_human_validation_of_artifact_hash`; both values must match."
                    ),
                    "task_id": task_id,
                    "blocked_by": "irreversible_artifact_hash",
                    "debt_id": debt.get("id"),
                    "debt_type": "irreversible_artifact_hash_unverified",
                    "hash_status": hash_reason,
                },
                ensure_ascii=False,
                indent=2,
            )

    if (task.get("task_type") or "").strip() == "analyze" and clean_outcome == "done":
        artifact_paths = _existing_analyze_artifact_paths(all_evidence_refs)
        finding_count, finding_artifacts = _count_p0_p1_findings(artifact_paths)
        followup_ref_count = _count_followup_refs(all_evidence_refs)
        if finding_count > followup_ref_count:
            missing = finding_count - followup_ref_count
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "analyze_p0_p1_followups_missing",
                severity="error",
                evidence=(
                    f"Analyze task produced {finding_count} P0/P1 finding(s) in report artifact(s) "
                    f"but evidence_refs only contained {followup_ref_count} followup id(s); "
                    f"{missing} actionable finding(s) would be left without durable followup. "
                    f"Artifacts: {json.dumps(finding_artifacts, ensure_ascii=False)}"
                ),
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close analyze task as 'done' while P0/P1 report findings lack followup refs.",
                    "hint": (
                        "Create one followup for each P0/P1 finding and pass those followup IDs in evidence_refs, "
                        "then retry nexo_task_close."
                    ),
                    "task_id": task_id,
                    "blocked_by": "analyze_p0_p1_followup_gate",
                    "debt_id": debt.get("id"),
                    "debt_type": "analyze_p0_p1_followups_missing",
                    "findings": finding_count,
                    "followup_refs": followup_ref_count,
                    "missing_followups": missing,
                    "artifacts": finding_artifacts,
                },
                ensure_ascii=False,
                indent=2,
            )

    original_symptom_evidence = "\n".join(
        part
        for part in (clean_evidence, clean_change_verify, outcome_notes, verification, summary, result)
        if part
    )
    if (
        clean_outcome == "done"
        and _requires_original_symptom_verification(task, closure_text)
        and not _has_original_symptom_verification(original_symptom_evidence)
    ):
        debt = _ensure_open_debt(
            task["session_id"],
            task_id,
            "verify_original_symptom_missing",
            severity="error",
            evidence=(
                "UI release-ready claim attempted without evidence that the original symptom was reproduced. "
                f"Goal: {task.get('goal','')}. Evidence provided: {original_symptom_evidence[:240]!r}"
            ),
            debts=debts_created,
        )
        return json.dumps(
            {
                "ok": False,
                "error": "Cannot close UI release as done without original-symptom verification evidence.",
                "hint": (
                    "Attach proof that the reported symptom itself was reopened and reproduced/verified: "
                    "repro URL, headed screenshot/browser evidence, Playwright output, or curl output."
                ),
                "task_id": task_id,
                "blocked_by": "verify_original_symptom",
                "debt_id": debt.get("id"),
                "debt_type": "verify_original_symptom_missing",
            },
            ensure_ascii=False,
            indent=2,
        )

    desktop_promise_audit_evidence = "\n".join(
        part
        for part in (
            clean_evidence,
            clean_change_verify,
            outcome_notes,
            verification,
            summary,
            result,
            evidence_refs,
            clean_change_summary,
            change_why,
        )
        if part
    )
    desktop_promise_missing = (
        _missing_desktop_release_promise_audit_evidence(desktop_promise_audit_evidence)
        if clean_outcome == "done" and is_desktop_release_close
        else []
    )
    if desktop_promise_missing:
        debt = _ensure_open_debt(
            task["session_id"],
            task_id,
            "desktop_release_promise_audit_missing",
            severity="error",
            evidence=(
                "NEXO Desktop release close attempted without the pre-release open-promise checklist. "
                f"Missing: {', '.join(desktop_promise_missing)}. Goal: {task.get('goal','')}. "
                f"Evidence provided: {desktop_promise_audit_evidence[:300]!r}"
            ),
            debts=debts_created,
        )
        return json.dumps(
            {
                "ok": False,
                "error": "Cannot close a NEXO Desktop release without the open-promise pre-release audit.",
                "hint": (
                    "Attach evidence that today's transcript was searched for release promises, "
                    "dist/release or the packaged bundle was searched for each promised feature, "
                    "and every missing promise was converted into a persistent NF followup."
                ),
                "task_id": task_id,
                "blocked_by": "desktop_release_promise_audit",
                "missing_evidence": desktop_promise_missing,
                "debt_id": debt.get("id"),
                "debt_type": "desktop_release_promise_audit_missing",
            },
            ensure_ascii=False,
            indent=2,
        )

    p0_evidence_text = "\n".join(
        part
        for part in (
            clean_evidence,
            clean_change_verify,
            outcome_notes,
            verification,
            summary,
            result,
            evidence_refs,
            clean_change_summary,
        )
        if part
    )
    p0_missing = (
        _missing_repeated_symptom_p0_evidence(p0_evidence_text)
        if clean_outcome == "done" and _is_repeated_symptom_p0_task(task, closure_text)
        else []
    )
    if p0_missing:
        debt = _ensure_open_debt(
            task["session_id"],
            task_id,
            "p0_repeated_bug_evidence_missing",
            severity="error",
            evidence=(
                "Repeated-symptom/P0 bug close attempted without the required three evidence classes. "
                f"Missing: {', '.join(p0_missing)}. Goal: {task.get('goal','')}. "
                f"Evidence provided: {p0_evidence_text[:300]!r}"
            ),
            debts=debts_created,
        )
        return json.dumps(
            {
                "ok": False,
                "error": "Cannot close repeated-symptom P0 bug without reproducer test, backend flow verification, and UI post-fix evidence.",
                "hint": (
                    "Attach all three: (a) reproducer regression test that failed pre-fix and passes post-fix, "
                    "(b) backend flow verification with curl or SQL, and (c) UI post-fix screenshot/log evidence."
                ),
                "task_id": task_id,
                "blocked_by": "p0_repeated_bug_evidence",
                "priority": "P0",
                "missing_evidence": p0_missing,
                "debt_id": debt.get("id"),
                "debt_type": "p0_repeated_bug_evidence_missing",
            },
            ensure_ascii=False,
            indent=2,
        )

    live_surface_required = _requires_live_surface_verification(task, clean_outcome)
    live_surface_evidence = "\n".join(
        part
        for part in (clean_evidence, clean_change_verify, outcome_notes, verification, summary, result)
        if part
    )
    if live_surface_required and not _has_live_surface_verification(live_surface_evidence):
        debt = _ensure_open_debt(
            task["session_id"],
            task_id,
            "live_surface_verification_missing",
            severity="error",
            evidence=(
                "Task closed as done for state/storefront/backend/data behavior without live-surface evidence. "
                f"Goal: {task.get('goal','')}. Evidence provided: {live_surface_evidence[:240]!r}"
            ),
            debts=debts_created,
        )
        return json.dumps(
            {
                "ok": False,
                "error": "Cannot close task as 'done' without live production verification evidence.",
                "hint": (
                    "Add evidence from the real surface: live logs such as black-box.ndjson/impossible_state_recovered, "
                    "published storefront/browser or Playwright evidence, or production database evidence."
                ),
                "task_id": task_id,
                "blocked_by": "live_surface_verification",
                "debt_id": debt.get("id"),
                "debt_type": "live_surface_verification_missing",
            },
            ensure_ascii=False,
            indent=2,
        )

    pending_corrections = list_session_correction_requirements(
        session_id=task["session_id"],
        status="open",
        limit=3,
    )
    correction_justification_text = "\n".join(
        part
        for part in (learning_reasoning, outcome_notes, clean_evidence, clean_change_verify, result, summary)
        if part
    )
    correction_justified_without_learning = _has_correction_no_learning_justification(correction_justification_text)
    if pending_corrections:
        learning_in_this_close = bool(
            (learning_title or "").strip() and (learning_content or "").strip()
        )
        auto_learning_possible = bool(
            correction
            and (
                clean_change_summary
                or clean_evidence
                or outcome_notes
                or change_why
            )
        )
        if correction_justified_without_learning:
            _resolve_correction_requirements_with_justification(
                task["session_id"],
                task_id,
                correction_justification_text,
            )
        elif not (learning_in_this_close or auto_learning_possible):
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "missing_learning_after_correction",
                severity="error",
                evidence=(
                    "User correction detected for this session without a durable learning "
                    "or an explicit no-learning justification."
                ),
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close after a detected user correction without a persisted learning.",
                    "hint": (
                        "Call `nexo_learning_add(...)` first, or close with `correction_happened=true` plus "
                        "`learning_title` and `learning_content`. Use an explicit no-learning justification only "
                        "for detector false positives or cases with no reusable rule."
                    ),
                    "task_id": task_id,
                    "blocked_by": "correction_learning_required",
                    "debt_id": debt.get("id"),
                    "debt_type": "missing_learning_after_correction",
                },
                ensure_ascii=False,
                indent=2,
            )

    learning_trace_text = "\n".join(
        part
        for part in (
            learning_title,
            learning_content,
            learning_reasoning,
            clean_evidence,
            clean_change_summary,
            clean_change_verify,
            outcome_notes,
            result,
            summary,
            verification,
            evidence_refs,
        )
        if part
    )
    learning_payload_present = bool((learning_title or "").strip() and (learning_content or "").strip())
    if (
        _requires_learning_after_medium_impact_edit(
            task=task,
            clean_outcome=clean_outcome,
            effective_files=effective_files,
            closure_text=close_payload_text,
        )
        and not learning_payload_present
        and not _close_payload_has_learning_trace(learning_trace_text)
        and not correction_justified_without_learning
    ):
        debt = _ensure_open_debt(
            task["session_id"],
            task_id,
            "missing_learning_after_medium_impact_edit",
            severity="error",
            evidence=(
                "Task close attempted after a medium-impact edit/execute that looked like a bugfix "
                "or reusable solution, but no learning payload or prior nexo_learning_add reference was supplied. "
                f"Files: {', '.join(effective_files)[:300]}. Claim: {closure_text[:240]!r}"
            ),
            debts=debts_created,
        )
        return json.dumps(
            {
                "ok": False,
                "error": "Cannot close medium-impact edited work without a persisted learning or explicit no-learning justification.",
                "hint": (
                    "Call `nexo_learning_add(...)` first, pass `learning_title` and `learning_content`, "
                    "or add a concrete no-learning justification when the edit taught nothing reusable."
                ),
                "task_id": task_id,
                "blocked_by": "learning_required_after_medium_impact_edit",
                "debt_id": debt.get("id"),
                "debt_type": "missing_learning_after_medium_impact_edit",
            },
            ensure_ascii=False,
            indent=2,
        )

    # ── Evidence enforcement: reject 'done' without proof ──
    # G1 hardening: "done" is no longer allowed to degrade into a debt-only
    # close when verify evidence is missing. Keep the task open, open/dedupe
    # the debt, and force the caller to provide real proof before closing.
    if task.get("must_verify") and clean_outcome == "done":
        is_trivial, trivial_reason = _is_trivial_evidence(clean_evidence)
        if not is_trivial:
            resolve_protocol_debts(
                task_id=task_id,
                debt_types=["claimed_done_without_evidence"],
                resolution="Verification evidence supplied during task_close",
            )
        else:
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "claimed_done_without_evidence",
                severity="error",
                evidence=(
                    f"Task closed as done with trivial evidence "
                    f"({trivial_reason}). Goal: {task.get('goal','')}. "
                    f"Evidence provided: {clean_evidence[:200]!r}"
                ),
                debts=debts_created,
            )
            if trivial_reason == "empty":
                err = "Cannot close task as 'done' without evidence."
                hint = (
                    "Provide the `evidence` parameter with verifiable proof: "
                    "test output, curl response, screenshot path, or real "
                    "command output."
                )
            else:
                err = (
                    "Cannot close task as 'done' with trivial evidence "
                    f"({trivial_reason})."
                )
                hint = (
                    f"Evidence must be substantive: >= {R03_MIN_EVIDENCE_CHARS} "
                    "characters AND not a single filler word. Attach real "
                    "proof — test output excerpt, curl response, DB row, "
                    "screenshot path, or command stdout."
                )
            return json.dumps(
                {
                    "ok": False,
                    "error": err,
                    "hint": hint,
                    "task_id": task_id,
                    "blocked_by": "g1_verify",
                    "debt_id": debt.get("id"),
                    "debt_type": "claimed_done_without_evidence",
                    "evidence_quality_reason": trivial_reason,
                    "protocol_strictness": get_protocol_strictness(),
                },
                ensure_ascii=False,
                indent=2,
            )

    if clean_outcome == "done" and _requires_external_real_world_check(
        task,
        clean_evidence,
        clean_change_verify,
        outcome_notes,
        clean_change_summary,
    ):
        real_world_evidence = "\n".join(
            part
            for part in (
                clean_evidence,
                clean_change_verify,
                outcome_notes,
                clean_change_summary,
            )
            if part
        )
        if _has_external_real_world_evidence(real_world_evidence):
            resolve_protocol_debts(
                task_id=task_id,
                debt_types=["external_real_world_verification_missing"],
                resolution="task_close evidence includes post-action real-world verification.",
            )
        else:
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "external_real_world_verification_missing",
                severity="error",
                evidence=(
                    "External-stakes task closed as done without proof that the real sent/event/booking "
                    f"artifact was reopened and verified. Goal: {task.get('goal','')}. "
                    f"Evidence provided: {real_world_evidence[:240]!r}"
                ),
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close external-stakes task as 'done' without post-action real-world verification.",
                    "hint": (
                        "Re-open the sent email/message/calendar/booking artifact and verify recipients, "
                        "CC/BCC, subject, body/signature, date/time/timezone, links, invitees, and attachments as applicable. "
                        "Then retry nexo_task_close with that evidence."
                    ),
                    "task_id": task_id,
                    "blocked_by": "external_real_world_verify",
                    "debt_id": debt.get("id"),
                    "debt_type": "external_real_world_verification_missing",
                },
                ensure_ascii=False,
                indent=2,
            )

    # ── Release checklist: require channel alignment evidence for release tasks ──
    is_release = _is_release_task(
        goal=task.get("goal") or "",
        area=task.get("area") or "",
        project_hint=task.get("project_hint") or "",
        verification_step=task.get("verification_step") or "",
    )
    if is_release and clean_outcome == "done" and clean_evidence:
        missing_channels: list[str] = []
        evidence_lower = clean_evidence.lower()
        for channel in ["test", "staging", "production", "changelog", "version"]:
            if channel not in evidence_lower:
                missing_channels.append(channel)
        if missing_channels:
            _record_debt(
                task["session_id"],
                task_id,
                "release_channel_alignment_incomplete",
                severity="warn",
                evidence=f"Release task evidence missing channel references: {', '.join(missing_channels)}. Evidence provided: {clean_evidence[:200]}",
                debts=debts_created,
            )

    production_change_log_required = _requires_production_change_log(
        task,
        clean_outcome,
        clean_evidence,
        clean_change_summary,
        triggered_by,
    )
    if (task.get("must_change_log") or production_change_log_required) and clean_outcome in {"done", "partial", "failed"}:
        if effective_files:
            change = log_change(
                task["session_id"],
                ", ".join(effective_files),
                (clean_change_summary or f"Protocol task {task_id}: {task.get('goal', '')}")[:500],
                (change_why or task.get("goal", ""))[:500],
                (triggered_by or task_id)[:200],
                task.get("area", "")[:200],
                (change_risks or "")[:500],
                (clean_change_verify or clean_evidence)[:500],
            )
            if "error" in change:
                debt = _ensure_open_debt(
                    task["session_id"],
                    task_id,
                    "missing_change_log",
                    severity="warn",
                    evidence=f"change_log failed: {change['error']}",
                    debts=debts_created,
                )
                if clean_outcome == "done":
                    return json.dumps(
                        {
                            "ok": False,
                            "error": "Cannot close task as 'done' because change_log creation failed.",
                            "hint": "Capture the changed files and create the change log successfully before closing as done.",
                            "task_id": task_id,
                            "blocked_by": "g1_change_log",
                            "debt_id": debt.get("id"),
                            "debt_type": "missing_change_log",
                            "change_log_error": change.get("error"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
            else:
                change_log_id = change.get("id")
                resolve_protocol_debts(
                    task_id=task_id,
                    debt_types=["missing_change_log"],
                    resolution="Change log created by nexo_task_close",
                )
                # Cognitive OS Ola 1 — materialize causal/provenance edges from the
                # closed task (task→change_log "ops:produced" + change_log→task
                # "causal:motivated_by"). record_task_close_edges had NO caller, so
                # the causal graph stayed empty (0 candidates) and could never feed
                # connect-the-dots at answer time. Best-effort: graph wiring must
                # never break a task close.
                try:
                    import causal_graph
                    causal_graph.record_task_close_edges(
                        task_id=task_id,
                        change_log_id=change_log_id,
                        project_key=str(task.get("project_hint") or task.get("area") or ""),
                        reason_public=(clean_change_summary or task.get("goal") or "")[:200],
                    )
                except Exception:
                    pass
        else:
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "missing_change_log",
                severity="warn",
                evidence="Task required change_log but no changed files were supplied or recorded.",
                debts=debts_created,
            )
            if clean_outcome == "done":
                return json.dumps(
                    {
                        "ok": False,
                        "error": "Cannot close task as 'done' without changed files for the required change_log.",
                        "hint": "Pass `files_changed` (or open the task with files) so nexo_task_close can persist the change log before closing as done.",
                        "task_id": task_id,
                        "blocked_by": "g1_change_log",
                        "debt_id": debt.get("id"),
                        "debt_type": "missing_change_log",
                    },
                    ensure_ascii=False,
                    indent=2,
                )

    if correction:
        if (learning_title or "").strip() and (learning_content or "").strip():
            learning = _capture_learning(
                task,
                task_id,
                effective_files,
                category=(learning_category or task.get("area") or "nexo-ops"),
                title=learning_title.strip(),
                content=learning_content.strip(),
                reasoning=(learning_reasoning or f"Captured from protocol task {task_id}").strip(),
                priority="high",
            )
            if not learning.get("ok"):
                debt = _ensure_open_debt(
                    task["session_id"],
                    task_id,
                    "missing_learning_after_correction",
                    severity="error",
                    evidence=f"learning_add failed: {learning.get('error', 'unknown error')}",
                    debts=debts_created,
                )
                if not correction_justified_without_learning:
                    return json.dumps(
                        {
                            "ok": False,
                            "error": "Cannot close corrected work because the learning could not be persisted.",
                            "hint": "Fix the learning payload or provide an explicit no-learning justification, then retry nexo_task_close.",
                            "task_id": task_id,
                            "blocked_by": "correction_learning_persist_failed",
                            "debt_id": debt.get("id"),
                            "debt_type": "missing_learning_after_correction",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
            else:
                learning_id = learning.get("id")
                resolve_protocol_debts(
                    task_id=task_id,
                    debt_types=["missing_learning_after_correction"],
                    resolution="Learning captured during task_close",
                )
                if pending_corrections:
                    try:
                        from db import resolve_session_correction_requirements  # type: ignore

                        resolve_session_correction_requirements(
                            session_id=task["session_id"],
                            learning_id=int(learning_id or 0) or None,
                        )
                    except Exception:
                        pass
                if learning.get("superseded_id"):
                    resolve_protocol_debts(
                        task_id=task_id,
                        debt_types=["unacknowledged_guard_blocking"],
                        resolution=f"Guard blocking rule superseded by canonical learning #{learning_id}",
                    )
        else:
            auto_learning = _auto_capture_learning(
                task,
                task_id,
                effective_files,
                clean_evidence=clean_evidence,
                change_summary=clean_change_summary,
                change_why=change_why,
                outcome_notes=outcome_notes,
            )
            if auto_learning.get("ok"):
                learning_id = auto_learning.get("id")
                resolve_protocol_debts(
                    task_id=task_id,
                    debt_types=["missing_learning_after_correction"],
                    resolution="Learning auto-captured during task_close",
                )
                if pending_corrections:
                    try:
                        from db import resolve_session_correction_requirements  # type: ignore

                        resolve_session_correction_requirements(
                            session_id=task["session_id"],
                            learning_id=int(learning_id or 0) or None,
                        )
                    except Exception:
                        pass
                if auto_learning.get("superseded_id"):
                    resolve_protocol_debts(
                        task_id=task_id,
                        debt_types=["unacknowledged_guard_blocking"],
                        resolution=f"Guard blocking rule superseded by canonical learning #{learning_id}",
                    )
            elif correction_justified_without_learning:
                _resolve_correction_requirements_with_justification(
                    task["session_id"],
                    task_id,
                    correction_justification_text,
                )
            else:
                debt = _ensure_open_debt(
                    task["session_id"],
                    task_id,
                    "missing_learning_after_correction",
                    severity="error",
                    evidence=f"Task was marked as corrected but reusable learning capture failed: {auto_learning.get('error', 'missing payload')}",
                    debts=debts_created,
                )
                return json.dumps(
                    {
                        "ok": False,
                        "error": "Cannot close corrected work without a persisted learning or explicit no-learning justification.",
                        "hint": (
                            "Pass learning_title + learning_content, strengthen change_summary/evidence so auto-capture can persist, "
                            "or include an explicit no-learning justification."
                        ),
                        "task_id": task_id,
                        "blocked_by": "correction_learning_required",
                        "debt_id": debt.get("id"),
                        "debt_type": "missing_learning_after_correction",
                        "auto_capture_error": auto_learning.get("error", "missing payload"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )

    if followup_required:
        description = (followup_description or "").strip()
        if description:
            followup = create_followup(
                (followup_id or _auto_followup_id()).strip(),
                description,
                date=(followup_date or None),
                verification=(followup_verification or "").strip(),
                reasoning=(followup_reasoning or f"Created from protocol task {task_id}").strip(),
            )
            if "error" in followup:
                _record_debt(
                    task["session_id"],
                    task_id,
                    "missing_followup_payload",
                    severity="warn",
                    evidence=f"followup create failed: {followup['error']}",
                    debts=debts_created,
                )
            else:
                created_followup_id = followup.get("id", "")
        else:
            _record_debt(
                task["session_id"],
                task_id,
                "missing_followup_payload",
                severity="warn",
                evidence="followup_needed=true but no followup_description was supplied.",
                debts=debts_created,
            )

    if clean_outcome == "done" and _has_open_commitment_without_followup_signal(closure_text):
        if not _has_complete_followup_for_open_commitment(
            created_followup_id,
            followup_id,
            followup_description,
            evidence_refs,
            outcome_notes,
            result,
            summary,
            verification,
            clean_evidence,
            clean_change_summary,
            clean_change_verify,
        ):
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "open_commitment_without_complete_followup",
                severity="error",
                evidence=(
                    "Task close attempted done while leaving a commitment, deferred idea, or security/auth blocker "
                    f"without a followup carrying date, deliverable, and verification. Text: {closure_text[:240]!r}"
                ),
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close as done while an open commitment lacks a complete dated followup.",
                    "hint": (
                        "Create or link an NF followup with a concrete date, an actionable deliverable in the description, "
                        "and verification/evidence criteria, then retry task_close."
                    ),
                    "task_id": task_id,
                    "blocked_by": "open_commitment_followup_gate",
                    "debt_id": debt.get("id"),
                    "debt_type": "open_commitment_without_complete_followup",
                },
                ensure_ascii=False,
                indent=2,
            )

    if clean_outcome == "done" and _has_time_bound_commitment(closure_text):
        if not _has_dated_followup_for_time_bound_commitment(
            created_followup_id,
            followup_id,
            followup_description,
            evidence_refs,
            outcome_notes,
            result,
            summary,
            verification,
            clean_evidence,
            clean_change_summary,
            clean_change_verify,
        ):
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "time_bound_commitment_without_dated_followup",
                severity="error",
                evidence=(
                    "Task close attempted done while making a time-bound commitment "
                    f"without a dated followup/reminder. Text: {closure_text[:240]!r}"
                ),
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close as done while a time-bound commitment lacks a dated followup or reminder.",
                    "hint": (
                        "Create or link a followup/reminder with a concrete date for the promised check, "
                        "then retry task_close."
                    ),
                    "task_id": task_id,
                    "blocked_by": "time_bound_commitment_followup_gate",
                    "debt_id": debt.get("id"),
                    "debt_type": "time_bound_commitment_without_dated_followup",
                },
                ensure_ascii=False,
                indent=2,
            )

    if clean_outcome == "done" and _manual_pending_close_requires_followup(closure_text):
        if not _has_followup_for_manual_pending(
            created_followup_id,
            followup_id,
            followup_description,
            evidence_refs,
            outcome_notes,
            result,
            summary,
            verification,
            clean_evidence,
            clean_change_summary,
            clean_change_verify,
        ):
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "manual_pending_steps_without_followup",
                severity="error",
                evidence=(
                    "Task close attempted done while visible response text left manual steps pending "
                    f"without a linked followup. Text: {closure_text[:240]!r}"
                ),
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close as done while manual steps remain pending without a followup.",
                    "hint": (
                        "Create or link a concrete followup for the pending manual step, "
                        "or close as partial until the step is actually verified."
                    ),
                    "task_id": task_id,
                    "blocked_by": "manual_pending_followup_gate",
                    "debt_id": debt.get("id"),
                    "debt_type": "manual_pending_steps_without_followup",
                },
                ensure_ascii=False,
                indent=2,
            )

    if requires_decision_support and clean_outcome in {"done", "partial", "failed"}:
        if task_has_cortex_evaluation(task_id):
            resolve_protocol_debts(
                task_id=task_id,
                debt_types=["missing_cortex_evaluation"],
                resolution="High-stakes action task has a persisted Cortex evaluation.",
            )
        else:
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "missing_cortex_evaluation",
                severity="error",
                evidence="High-stakes action task closed without nexo_cortex_decide / persisted evaluation.",
                debts=debts_created,
            )
            if clean_outcome == "done":
                return json.dumps(
                    {
                        "ok": False,
                        "error": "Cannot close high-stakes action task as 'done' without a persisted cortex evaluation.",
                        "hint": "Run `nexo_cortex_decide(...)` for this task and then close it again with the final evidence.",
                        "task_id": task_id,
                        "blocked_by": "g1_cortex",
                        "debt_id": debt.get("id"),
                        "debt_type": "missing_cortex_evaluation",
                    },
                    ensure_ascii=False,
                    indent=2,
                )

    if (
        clean_outcome == "done"
        and _is_high_stakes_public_work(task, work_type, stakes, closure_text)
        and not _has_public_release_evidence(live_surface_evidence)
    ):
        debt = _ensure_open_debt(
            task["session_id"],
            task_id,
            "high_stakes_public_evidence_missing",
            severity="error",
            evidence=(
                "High-stakes release/deploy/publish close lacked screenshot, public URL, or curl HTTP 200 evidence. "
                f"Evidence provided: {live_surface_evidence[:240]!r}"
            ),
            debts=debts_created,
        )
        return json.dumps(
            {
                "ok": False,
                "error": "Cannot close high-stakes release/deploy/publish as done without public verification evidence.",
                "hint": "Attach screenshot path, public URL evidence, or curl output showing HTTP 200, then retry.",
                "task_id": task_id,
                "blocked_by": "high_stakes_public_evidence",
                "debt_id": debt.get("id"),
                "debt_type": "high_stakes_public_evidence_missing",
                "response_mode": "verify",
            },
            ensure_ascii=False,
            indent=2,
        )

    if clean_outcome == "done" and _is_high_stakes_public_work(task, work_type, stakes, closure_text):
        missing_ux_items = _missing_ux_first_release_gate_items(live_surface_evidence)
        if missing_ux_items:
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "ux_first_release_gate_incomplete",
                severity="error",
                evidence=(
                    "High-stakes release/deploy/publish close lacked the UX-first release checklist. "
                    f"Missing items: {', '.join(missing_ux_items)}. "
                    f"Evidence provided: {live_surface_evidence[:240]!r}"
                ),
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close high-stakes release/deploy/publish as done without UX-first public validation.",
                    "hint": (
                        "Attach a screenshot or visual public-surface proof, one end-to-end final-user flow, "
                        "and the operator's specific success criterion for this release."
                    ),
                    "task_id": task_id,
                    "blocked_by": "ux_first_release_gate",
                    "debt_id": debt.get("id"),
                    "debt_type": "ux_first_release_gate_incomplete",
                    "missing_items": missing_ux_items,
                    "response_mode": "verify",
                },
                ensure_ascii=False,
                indent=2,
            )

    if clean_outcome == "done" and _is_high_stakes_public_work(task, work_type, stakes, closure_text):
        missing_surfaces = _missing_visible_release_surfaces(live_surface_evidence)
        if missing_surfaces:
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "visible_release_surface_matrix_incomplete",
                severity="error",
                evidence=(
                    "Visible release/deploy/fix close lacked the full evidence matrix. "
                    f"Missing surfaces: {', '.join(missing_surfaces)}. "
                    f"Evidence provided: {live_surface_evidence[:240]!r}"
                ),
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close visible release/deploy/fix as done without the full surface evidence matrix.",
                    "hint": (
                        "Enumerate API, UI, public domain, publication branch, artifacts, manifests, "
                        "and live test evidence. Use N/A only for a surface that truly does not exist."
                    ),
                    "task_id": task_id,
                    "blocked_by": "visible_release_surface_matrix",
                    "debt_id": debt.get("id"),
                    "debt_type": "visible_release_surface_matrix_incomplete",
                    "missing_surfaces": missing_surfaces,
                    "response_mode": "verify",
                },
                ensure_ascii=False,
                indent=2,
            )

    missing_verified_against_real = _missing_verified_against_real_items(verification_evidence)
    if _requires_verified_against_real_checklist(
        task,
        outcome_candidate,
        clean_outcome,
        work_type,
        stakes,
        closure_text,
    ) and missing_verified_against_real:
        partial_ack = _parse_bool(partial_verification_acknowledged)
        partial_reason = (partial_verification_reason or outcome_notes or "").strip()
        if clean_outcome == "partial" and partial_ack and partial_reason:
            clean_evidence = (
                f"{clean_evidence}\n"
                f"{PARTIAL_VERIFICATION_ACK_TOKEN}: {partial_reason}\n"
                f"missing_verified_against_real: {', '.join(missing_verified_against_real)}"
            ).strip()
        else:
            debt = _ensure_open_debt(
                task["session_id"],
                task_id,
                "verified_against_real_missing",
                severity="error",
                evidence=(
                    "Production execute close lacked verified-against-real checklist. "
                    f"Missing items: {', '.join(missing_verified_against_real)}. "
                    f"verification_evidence={str(verification_evidence)[:240]!r}"
                ),
                debts=debts_created,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot close production publish/deploy as verified without the verified-against-real checklist.",
                    "hint": (
                        "Pass verification_evidence[] with: escenario reproducido, datos REALES usados, "
                        "and hipótesis NO reproducidas. If verification is partial, close with outcome='partial', "
                        "partial_verification_acknowledged=true, and partial_verification_reason."
                    ),
                    "task_id": task_id,
                    "blocked_by": "verified_against_real",
                    "debt_id": debt.get("id"),
                    "debt_type": "verified_against_real_missing",
                    "missing_items": missing_verified_against_real,
                    "ack_required": PARTIAL_VERIFICATION_ACK_TOKEN,
                    "response_mode": "verify",
                },
                ensure_ascii=False,
                indent=2,
            )

    if task.get("guard_has_blocking") and not files_changed_list:
        open_task_debts = list_protocol_debts(status="open", task_id=task_id, limit=200)
        has_guard_touch_violation = any(
            (debt.get("debt_type") or "") in _GUARD_TOUCH_DEBT_TYPES
            for debt in open_task_debts
        )
        if not has_guard_touch_violation:
            resolve_protocol_debts(
                task_id=task_id,
                debt_types=["unacknowledged_guard_blocking"],
                resolution="Task closed without touching guarded files.",
            )

    task = close_protocol_task(
        task_id,
        outcome=clean_outcome,
        evidence=clean_evidence,
        files_changed=effective_files,
        correction_happened=correction,
        change_log_id=change_log_id,
        learning_id=learning_id,
        followup_id=created_followup_id,
        outcome_notes=outcome_notes,
    )

    # ── Ola 2: auto-detect a PRIOR own action that this close reveals as
    # wrong (e.g. code shipped earlier but the cron was never created). On
    # high-confidence objective evidence, capture an immediate learning +
    # prevention rule (source_authority=code_test_evidence, not a Francisco
    # correction); on low confidence, only a quiet INFO candidate. Strictly
    # best-effort — runs after the task is already persisted-closed.
    self_error = _detect_and_capture_self_error(
        task,
        task_id,
        clean_outcome=clean_outcome,
        closure_text=closure_text,
        correction=correction,
        effective_files=effective_files,
        forgotten_step_followup=_followup_signals_forgotten_step(
            followup_description, outcome_notes
        ),
        debts_created=debts_created,
    )
    capture_context_event(
        event_type=f"protocol_task_{clean_outcome}",
        title=(task.get("goal") or task_id)[:160],
        summary=(outcome_notes or clean_evidence or clean_outcome)[:600],
        body=(clean_change_summary or change_why or "")[:1600],
        context_key=f"protocol_task:{task_id}",
        context_title=(task.get("goal") or task_id)[:160],
        context_summary=(task.get("context_hint") or task.get("goal") or "")[:600],
        context_type="protocol_task",
        state="resolved" if clean_outcome in {"done", "cancelled"} else ("abandoned" if clean_outcome == "failed" else "blocked"),
        owner="nexo",
        actor=sid or task.get("session_id") or "nexo",
        source_type="protocol_task",
        source_id=task_id,
        session_id=task.get("session_id") or sid,
        metadata={
            "outcome": clean_outcome,
            "change_log_id": change_log_id,
            "learning_id": learning_id,
            "followup_id": created_followup_id,
        },
        ttl_hours=24,
    )
    memory_event = None
    try:
        from db import record_memory_event

        memory_event = record_memory_event(
            event_type=f"protocol_task_{clean_outcome}",
            source_type="protocol_task",
            source_id=task_id,
            session_id=task.get("session_id") or sid,
            project_key=task.get("project_hint") or task.get("area") or "",
            actor=sid or task.get("session_id") or "nexo",
            file_paths=effective_files,
            tool_input={
                "goal": task.get("goal") or "",
                "task_type": task.get("task_type") or "",
                "area": task.get("area") or "",
                "project_hint": task.get("project_hint") or "",
                "outcome": clean_outcome,
            },
            tool_output={
                "evidence": clean_evidence,
                "change_summary": clean_change_summary,
                "outcome_notes": outcome_notes,
            },
            raw_ref=f"protocol_task:{task_id}",
            privacy_level="normal",
            confidence=1.0,
            metadata={
                "change_log_id": change_log_id,
                "learning_id": learning_id,
                "followup_id": created_followup_id,
                "status": task.get("status"),
                "goal": task.get("goal") or "",
                "outcome": clean_outcome,
                "evidence_preview": clean_evidence[:600],
                "change_summary": clean_change_summary[:600],
            },
            idempotency_key=f"protocol_task:{task_id}:{clean_outcome}",
        )
    except Exception as exc:
        memory_event = {"ok": False, "error": str(exc)}
    # ── Drive/Curiosity: detect signals from task evidence (best-effort) ──
    try:
        _drive_text = " ".join(filter(None, [
            outcome_notes, clean_evidence, clean_change_summary, change_why,
        ]))
        if _drive_text and len(_drive_text.strip()) >= 15:
            from tools_drive import detect_drive_signal as _detect_drive
            _detect_drive(
                _drive_text[:600],
                source="task_close",
                source_id=task_id,
                area=task.get("area", ""),
            )
    except Exception:
        pass  # Drive detection is best-effort

    open_debts = list_protocol_debts(status="open", task_id=task_id, limit=20)
    # The self-error CANDIDATE debt is an informational, non-actionable signal
    # (low confidence; recorded for audit/dedup, never a learning). It must not
    # flip an otherwise-clean close into "done_with_debts" — that would be the
    # exact kind of noise/debt Francisco rejects.
    status_debts = [
        debt for debt in open_debts if debt.get("debt_type") != "self_error_candidate"
    ]

    status = "clean"
    next_action = "Task closed cleanly."
    if status_debts:
        if clean_outcome == "done":
            status = "done_with_debts"
            next_action = "Task closed as done, but resolve the open protocol debt next."
        else:
            status = "debt-open"
            next_action = "Resolve the open protocol debt next."

    durable_checkpoint = None
    try:
        from checkpoint_policy import record_milestone

        checkpoint_blockers = ""
        if clean_outcome in {"partial", "failed"}:
            checkpoint_blockers = (outcome_notes or clean_evidence or next_action).strip()
        durable_checkpoint = record_milestone(
            task.get("session_id") or sid,
            reason=f"task_close:{clean_outcome}",
            task=task.get("goal", ""),
            task_status=("blocked" if clean_outcome in {"partial", "failed"} else "active"),
            active_files=effective_files,
            current_goal=task.get("goal", ""),
            decisions_summary=(clean_change_summary or clean_outcome),
            blockers=checkpoint_blockers,
            reasoning_thread=(clean_evidence or outcome_notes or "")[:800],
            next_step=(next_action if clean_outcome != "done" else ""),
        )
    except Exception:
        durable_checkpoint = None

    response = {
        "ok": True,
        "task_id": task_id,
        "outcome": clean_outcome,
        "change_log_id": change_log_id,
        "learning_id": learning_id,
        "followup_id": created_followup_id,
        "cortex_evaluation": latest_cortex_evaluation_for_task(task_id) if requires_decision_support else None,
        "debts_created": debts_created,
        "open_debts": [
            {
                "id": debt.get("id"),
                "debt_type": debt.get("debt_type"),
                "severity": debt.get("severity"),
            }
            for debt in open_debts
        ],
        "status": status,
        "next_action": next_action,
        "memory_event": memory_event,
        "memory_event_ok": bool(memory_event and memory_event.get("ok")),
    }
    if self_error:
        response["self_error"] = self_error
    if durable_checkpoint:
        response["durable_checkpoint"] = durable_checkpoint
    return json.dumps(response, ensure_ascii=False, indent=2)


def handle_task_acknowledge_guard(
    sid: str,
    task_id: str,
    learning_ids: str = "",
    note: str = "",
) -> str:
    """Acknowledge blocking guard rules for an open protocol task."""
    task = get_protocol_task(task_id.strip())
    if not task:
        return json.dumps({"ok": False, "error": f"Unknown task_id: {task_id}"}, ensure_ascii=False, indent=2)
    if sid.strip() and task.get("session_id") and task["session_id"] != sid.strip():
        return json.dumps(
            {"ok": False, "error": f"Task {task_id} belongs to {task['session_id']}, not {sid}"},
            ensure_ascii=False,
            indent=2,
        )
    if not task.get("guard_has_blocking"):
        return json.dumps(
            {"ok": False, "error": f"Task {task_id} has no blocking guard rules to acknowledge."},
            ensure_ascii=False,
            indent=2,
        )
    if task.get("guard_acknowledged"):
        return json.dumps(
            {
                "ok": True,
                "task_id": task_id,
                "acknowledged_rule_ids": _extract_guard_blocking_ids(task.get("guard_summary") or ""),
                "resolved_debts": 0,
                "next_action": "Guard rules were already acknowledged for this task.",
            },
            ensure_ascii=False,
            indent=2,
        )

    expected = _extract_guard_blocking_ids(task.get("guard_summary") or "")
    provided = sorted({int(item) for item in _parse_list(learning_ids) if str(item).strip().isdigit()})
    if expected and sorted(expected) != provided:
        return json.dumps(
            {
                "ok": False,
                "error": "learning_ids must acknowledge every blocking rule on the task.",
                "expected_ids": expected,
                "provided_ids": provided,
            },
            ensure_ascii=False,
            indent=2,
        )

    set_protocol_task_guard_acknowledged(task_id, acknowledged=True)
    resolved = resolve_protocol_debts(
        task_id=task_id,
        debt_types=["unacknowledged_guard_blocking"],
        resolution=(note or f"Guard rules acknowledged: {provided}").strip(),
    )
    return json.dumps(
        {
            "ok": True,
            "task_id": task_id,
            "acknowledged_rule_ids": provided,
            "resolved_debts": resolved,
            "next_action": "Proceed with the task and close it with nexo_task_close once evidence is available.",
        },
        ensure_ascii=False,
        indent=2,
    )


def handle_protocol_debt_list(
    status: str = "open",
    task_id: str = "",
    session_id: str = "",
    debt_type: str = "",
    severity: str = "",
    limit: str = "50",
    sid: str = "",
) -> str:
    rows = list_protocol_debts(
        status=status.strip() if isinstance(status, str) else "open",
        task_id=(task_id or "").strip(),
        session_id=(session_id or sid or "").strip(),
        debt_type=(debt_type or "").strip(),
        severity=(severity or "").strip(),
        limit=max(1, min(500, int(limit or 50))),
    )
    summary: dict[str, int] = {}
    for row in rows:
        debt_key = str(row.get("debt_type") or "unknown")
        summary[debt_key] = summary.get(debt_key, 0) + 1
    return json.dumps(
        {
            "ok": True,
            "count": len(rows),
            "summary": summary,
            "items": rows,
        },
        ensure_ascii=False,
        indent=2,
    )


def handle_protocol_debt_resolve(
    debt_ids: str = "",
    task_id: str = "",
    session_id: str = "",
    debt_types: str = "",
    resolution: str = "",
    debt_id: str = "",
) -> str:
    parsed_ids = _parse_int_list(debt_ids)
    single_debt_id = (debt_id or "").strip()
    if single_debt_id:
        try:
            parsed_ids.append(int(single_debt_id))
        except ValueError:
            return json.dumps(
                {"ok": False, "error": f"Invalid debt_id: {single_debt_id}"},
                ensure_ascii=False,
                indent=2,
            )
    if parsed_ids:
        parsed_ids = sorted(set(parsed_ids))
    parsed_types = _parse_list(debt_types)
    if not parsed_ids and not (task_id or "").strip() and not (session_id or "").strip() and not parsed_types:
        return json.dumps(
            {
                "ok": False,
                "error": "Provide `debt_ids`, `task_id`, `session_id`, or `debt_types` to select protocol debt.",
            },
            ensure_ascii=False,
            indent=2,
        )

    matched: list[dict] = []
    if parsed_ids:
        conn = get_db()
        placeholders = ",".join("?" for _ in parsed_ids)
        rows = conn.execute(
            f"""SELECT * FROM protocol_debt
                WHERE status = 'open' AND id IN ({placeholders})
                ORDER BY created_at DESC""",
            tuple(parsed_ids),
        ).fetchall()
        matched = [dict(row) for row in rows]
    else:
        matched = list_protocol_debts(
            status="open",
            task_id=(task_id or "").strip(),
            session_id=(session_id or "").strip(),
            limit=500,
        )
        if parsed_types:
            allowed = set(parsed_types)
            matched = [row for row in matched if str(row.get("debt_type") or "") in allowed]

    normalized_resolution = (resolution or "Resolved during protocol debt maintenance audit.").strip()
    resolved = resolve_protocol_debts(
        task_id=(task_id or "").strip(),
        session_id=(session_id or "").strip(),
        debt_ids=parsed_ids or None,
        debt_types=parsed_types or None,
        resolution=normalized_resolution,
    )
    return json.dumps(
        {
            "ok": True,
            "resolved": resolved,
            "matched_ids": [int(row["id"]) for row in matched],
            "matched_debt_types": sorted({str(row.get("debt_type") or "") for row in matched if row.get("debt_type")}),
            "resolution": normalized_resolution,
        },
        ensure_ascii=False,
        indent=2,
    )


TOOLS = [
    (handle_confidence_check, "nexo_confidence_check", "Decide whether a non-trivial answer should be answered, verified, asked, or deferred before replying."),
    (handle_task_open, "nexo_task_open", "Open a non-trivial task with heartbeat, guard, rules, and Cortex captured as one protocol contract."),
    (handle_task_acknowledge_guard, "nexo_task_acknowledge_guard", "Acknowledge blocking guard rules on an open protocol task before proceeding."),
    (handle_task_close, "nexo_task_close", "Close a protocol task, auto-record evidence/change-log/followup artifacts, and open protocol debt when discipline is missing."),
    (handle_protocol_debt_list, "nexo_protocol_debt_list", "List protocol debt records with optional status, session, task, type, or severity filters."),
    (handle_protocol_debt_resolve, "nexo_protocol_debt_resolve", "Resolve protocol debt records by id or filters once the debt has been audited and cleared."),
]
