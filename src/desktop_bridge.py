"""Desktop bridge — read-only commands for NEXO Desktop (Electron UI).

Exposes four read-only commands so Desktop can auto-adapt its UI:
  nexo schema --json       → editable-field schema for Preferences UI
  nexo identity --json     → canonical assistant identity + source path
  nexo onboard --json      → onboarding wizard steps
  nexo scan-profile        → build profile.json from CLAUDE.md + memory

All commands honor NEXO_HOME. None mutate state unless --apply is passed
on scan-profile (default is dry-run: prints the proposed payload).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
ONBOARD_VERSION = 1


def _nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


def _calibration_path() -> Path:
    import paths
    return paths.brain_dir() / "calibration.json"


def _profile_path() -> Path:
    import paths
    return paths.brain_dir() / "profile.json"


def _read_json(path: Path) -> dict:
    try:
        if path.is_file():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


def _print_json(payload: Any) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


# ------------------------------------------------------------------ schema

def _schema_fields() -> list[dict]:
    """Canonical list of editable fields across calibration.json / profile.json.

    Each field declares its storage path (dot notation) and which file it
    lives in, so Desktop can read/write precisely without guessing.
    """
    return [
        {
            "path": "user.name",
            "file": "calibration.json",
            "label": {"es": "Nombre", "en": "Name"},
            "type": "text",
            "group": "personal",
        },
        {
            "path": "user.language",
            "file": "calibration.json",
            "label": {"es": "Idioma", "en": "Language"},
            "type": "select",
            "group": "personal",
            "options": [
                {"value": "es", "label": {"es": "Español", "en": "Spanish"}},
                {"value": "en", "label": {"es": "Inglés", "en": "English"}},
                {"value": "ca", "label": {"es": "Catalán", "en": "Catalan"}},
                {"value": "fr", "label": {"es": "Francés", "en": "French"}},
                {"value": "de", "label": {"es": "Alemán", "en": "German"}},
                {"value": "it", "label": {"es": "Italiano", "en": "Italian"}},
                {"value": "pt", "label": {"es": "Portugués", "en": "Portuguese"}},
            ],
        },
        {
            "path": "user.timezone",
            "file": "calibration.json",
            "label": {"es": "Zona horaria", "en": "Timezone"},
            "type": "text",
            "group": "personal",
            "default": "Europe/Madrid",
        },
        {
            "path": "user.assistant_name",
            "file": "calibration.json",
            "label": {"es": "Nombre del asistente", "en": "Assistant name"},
            "type": "text",
            "group": "personal",
            "default": "NEXO",
        },
        {
            "path": "personality.autonomy",
            "file": "calibration.json",
            "label": {"es": "Autonomía", "en": "Autonomy"},
            "type": "select",
            "group": "personality",
            "options": [
                {"value": "conservative", "label": {"es": "Conservadora", "en": "Conservative"}},
                {"value": "balanced", "label": {"es": "Equilibrada", "en": "Balanced"}},
                {"value": "full", "label": {"es": "Plena", "en": "Full"}},
            ],
        },
        {
            "path": "personality.communication",
            "file": "calibration.json",
            "label": {"es": "Comunicación", "en": "Communication"},
            "type": "select",
            "group": "personality",
            "options": [
                {"value": "concise", "label": {"es": "Concisa", "en": "Concise"}},
                {"value": "balanced", "label": {"es": "Equilibrada", "en": "Balanced"}},
                {"value": "detailed", "label": {"es": "Detallada", "en": "Detailed"}},
            ],
        },
        {
            "path": "personality.honesty",
            "file": "calibration.json",
            "label": {"es": "Honestidad", "en": "Honesty"},
            "type": "select",
            "group": "personality",
            "options": [
                {"value": "firm-pushback", "label": {"es": "Firme", "en": "Firm pushback"}},
                {"value": "mention-and-follow", "label": {"es": "Menciona y sigue", "en": "Mention and follow"}},
                {"value": "just-execute", "label": {"es": "Solo ejecuta", "en": "Just execute"}},
            ],
        },
        {
            "path": "personality.proactivity",
            "file": "calibration.json",
            "label": {"es": "Proactividad", "en": "Proactivity"},
            "type": "select",
            "group": "personality",
            "options": [
                {"value": "reactive", "label": {"es": "Reactiva", "en": "Reactive"}},
                {"value": "suggestive", "label": {"es": "Sugerente", "en": "Suggestive"}},
                {"value": "proactive", "label": {"es": "Proactiva", "en": "Proactive"}},
            ],
        },
        {
            "path": "personality.error_handling",
            "file": "calibration.json",
            "label": {"es": "Errores", "en": "Error handling"},
            "type": "select",
            "group": "personality",
            "options": [
                {"value": "brief-fix", "label": {"es": "Arreglo breve", "en": "Brief fix"}},
                {"value": "explain-and-learn", "label": {"es": "Explica y aprende", "en": "Explain and learn"}},
            ],
        },
        {
            "path": "preferences.menu_on_demand",
            "file": "calibration.json",
            "label": {"es": "Menú bajo demanda", "en": "Menu on demand"},
            "type": "boolean",
            "group": "preferences",
            "default": True,
        },
        {
            "path": "preferences.show_pending_items",
            "file": "calibration.json",
            "label": {"es": "Mostrar pendientes al inicio", "en": "Show pending items at startup"},
            "type": "boolean",
            "group": "preferences",
            "default": False,
        },
        {
            "path": "preferences.execution_first",
            "file": "calibration.json",
            "label": {"es": "Ejecuta antes de preguntar", "en": "Execute before asking"},
            "type": "boolean",
            "group": "preferences",
            "default": True,
        },
        {
            "path": "preferences.report_style",
            "file": "calibration.json",
            "label": {"es": "Estilo de reporte", "en": "Report style"},
            "type": "select",
            "group": "preferences",
            "options": [
                {"value": "essentials_only", "label": {"es": "Solo esencial", "en": "Essentials only"}},
                {"value": "balanced", "label": {"es": "Equilibrado", "en": "Balanced"}},
                {"value": "verbose", "label": {"es": "Detallado", "en": "Verbose"}},
            ],
        },
        {
            "path": "preferences.default_resonance",
            "file": "calibration.json",
            "label": {"es": "Resonancia por defecto", "en": "Default resonance"},
            "type": "select",
            "group": "preferences",
            "default": "alto",
            "hint": {
                "es": (
                    "Potencia del modelo para sesiones interactivas (nexo chat y "
                    "nueva conversación en Desktop). Los crons y procesos de fondo "
                    "(deep sleep, evolution, etc.) ignoran esta preferencia — los "
                    "definimos nosotros en resonance_map.py por calidad."
                ),
                "en": (
                    "Model power for interactive sessions (nexo chat and Desktop "
                    "new conversation). Crons and background processes (deep sleep, "
                    "evolution, etc.) ignore this preference — we pin them per "
                    "caller in resonance_map.py based on quality needs."
                ),
            },
            "options": [
                {"value": "maximo", "label": {"es": "Máximo", "en": "Maximum"}},
                {"value": "alto", "label": {"es": "Alto (recomendado)", "en": "High (recommended)"}},
                {"value": "medio", "label": {"es": "Medio", "en": "Medium"}},
                {"value": "bajo", "label": {"es": "Bajo", "en": "Low"}},
            ],
        },
        {
            "path": "meta.role",
            "file": "calibration.json",
            "label": {"es": "Rol / ocupación", "en": "Role / occupation"},
            "type": "text",
            "group": "about_you",
        },
        {
            "path": "meta.technical_level",
            "file": "calibration.json",
            "label": {"es": "Nivel técnico", "en": "Technical level"},
            "type": "select",
            "group": "about_you",
            "options": [
                {"value": "beginner", "label": {"es": "Principiante", "en": "Beginner"}},
                {"value": "intermediate", "label": {"es": "Intermedio", "en": "Intermediate"}},
                {"value": "advanced", "label": {"es": "Avanzado", "en": "Advanced"}},
            ],
        },
    ]


def _schema_groups() -> list[dict]:
    return [
        {"id": "personal", "label": {"es": "Personal", "en": "Personal"}, "order": 1},
        {"id": "personality", "label": {"es": "Personalidad", "en": "Personality"}, "order": 2},
        {"id": "preferences", "label": {"es": "Preferencias", "en": "Preferences"}, "order": 3},
        {"id": "about_you", "label": {"es": "Sobre ti", "en": "About you"}, "order": 4},
    ]


def cmd_schema(args) -> int:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "groups": _schema_groups(),
        "fields": _schema_fields(),
    }
    return _print_json(payload)


# ---------------------------------------------------------------- identity

def _resolve_identity() -> dict:
    """Resolve the canonical assistant name + which source produced it."""
    cal = _read_json(_calibration_path())
    prof = _read_json(_profile_path())

    probes: list[tuple[str, Any]] = [
        ("calibration.user.assistant_name",
         cal.get("user", {}).get("assistant_name") if isinstance(cal.get("user"), dict) else None),
        ("calibration.operator_name", cal.get("operator_name")),
        ("calibration.assistant_name", cal.get("assistant_name")),
        ("calibration.identity", cal.get("identity")),
        ("profile.operator_name", prof.get("operator_name")),
        ("profile.assistant_name", prof.get("assistant_name")),
    ]
    for source, value in probes:
        if isinstance(value, str) and value.strip():
            return {"name": value.strip(), "source": source,
                    "writable_source": "calibration.user.assistant_name"}

    return {"name": "NEXO", "source": "default",
            "writable_source": "calibration.user.assistant_name"}


def cmd_identity(args) -> int:
    return _print_json(_resolve_identity())


# ---------------------------------------------------------------- onboard

def _onboard_steps() -> list[dict]:
    return [
        {
            "id": "name",
            "prompt": {"es": "¿Cómo te llamas?", "en": "What's your name?"},
            "type": "text",
            "writes": "user.name",
            "file": "calibration.json",
            "optional": False,
            "validate": r"^\S.{0,60}$",
        },
        {
            "id": "language",
            "prompt": {"es": "¿En qué idioma quieres operar?", "en": "Which language should we use?"},
            "type": "select",
            "writes": "user.language",
            "file": "calibration.json",
            "optional": False,
            "default": "es",
            "options": [
                {"value": "es", "label": {"es": "Español", "en": "Spanish"}},
                {"value": "en", "label": {"es": "Inglés", "en": "English"}},
            ],
        },
        {
            "id": "assistant_name",
            "prompt": {"es": "¿Cómo se llamará tu asistente?", "en": "What will your assistant be called?"},
            "type": "text",
            "writes": "user.assistant_name",
            "file": "calibration.json",
            "optional": True,
            "default": "NEXO",
        },
        {
            "id": "role",
            "prompt": {"es": "¿A qué te dedicas?", "en": "What do you do?"},
            "type": "text",
            "writes": "meta.role",
            "file": "calibration.json",
            "optional": True,
        },
        {
            "id": "technical_level",
            "prompt": {"es": "¿Cuál es tu nivel técnico?", "en": "What's your technical level?"},
            "type": "select",
            "writes": "meta.technical_level",
            "file": "calibration.json",
            "optional": True,
            "default": "intermediate",
            "options": [
                {"value": "beginner", "label": {"es": "Principiante", "en": "Beginner"}},
                {"value": "intermediate", "label": {"es": "Intermedio", "en": "Intermediate"}},
                {"value": "advanced", "label": {"es": "Avanzado", "en": "Advanced"}},
            ],
        },
        {
            "id": "welcome",
            "type": "welcome",
            "message": {
                "es": "Listo. A partir de ahora aprendo observándote. Dime qué necesitas.",
                "en": "Ready. From now on I learn by watching. Tell me what you need.",
            },
        },
    ]


def cmd_onboard(args) -> int:
    payload = {
        "onboard_version": ONBOARD_VERSION,
        "steps": _onboard_steps(),
    }
    return _print_json(payload)


# ------------------------------------------------------------ scan-profile

# Patterns we try to lift from the user's CLAUDE.md into profile.json.
# Kept deliberately narrow to avoid false positives.
_CLAUDE_MD_CANDIDATES = [
    Path.home() / ".claude" / "CLAUDE.md",
    Path.home() / "CLAUDE.md",
]


def _read_claude_md() -> str:
    for p in _CLAUDE_MD_CANDIDATES:
        try:
            if p.is_file():
                return p.read_text(errors="ignore")
        except Exception:
            continue
    return ""


def _guess_role(claude_md: str, cal: dict) -> str:
    meta_role = cal.get("meta", {}).get("role") if isinstance(cal.get("meta"), dict) else None
    if isinstance(meta_role, str) and meta_role.strip():
        return meta_role.strip()
    m = re.search(r"(?im)^\s*[-*]?\s*(?:role|rol|ocupaci[oó]n|dedica)\s*[:=]\s*(.+)$", claude_md)
    if m:
        return m.group(1).strip().strip(".")
    return ""


def _guess_technical_level(claude_md: str, cal: dict) -> str:
    meta_tl = cal.get("meta", {}).get("technical_level") if isinstance(cal.get("meta"), dict) else None
    if isinstance(meta_tl, str) and meta_tl.strip():
        return meta_tl.strip()
    text = claude_md.lower()
    if "advanced" in text or "avanzado" in text or "senior" in text:
        return "advanced"
    if "intermediate" in text or "intermedio" in text:
        return "intermediate"
    if "beginner" in text or "principiante" in text:
        return "beginner"
    return ""


def _guess_timezone(cal: dict) -> str:
    tz = cal.get("user", {}).get("timezone") if isinstance(cal.get("user"), dict) else None
    if isinstance(tz, str) and tz.strip():
        return tz.strip()
    return os.environ.get("TZ", "") or ""


def _build_profile_payload() -> dict:
    cal = _read_json(_calibration_path())
    claude_md = _read_claude_md()

    user = cal.get("user", {}) if isinstance(cal.get("user"), dict) else {}
    payload = {
        "version": 1,
        "source": "scan-profile",
        "user": {
            "name": user.get("name", "") or "",
            "language": user.get("language", "") or "",
            "timezone": _guess_timezone(cal),
        },
        "meta": {
            "role": _guess_role(claude_md, cal),
            "technical_level": _guess_technical_level(claude_md, cal),
        },
        "signals": {
            "has_claude_md": bool(claude_md),
            "claude_md_chars": len(claude_md),
            "calibration_present": bool(cal),
        },
    }
    return payload


def cmd_scan_profile(args) -> int:
    profile_path = _profile_path()
    payload = _build_profile_payload()
    use_json = bool(getattr(args, "json", False))
    apply = bool(getattr(args, "apply", False))
    force = bool(getattr(args, "force", False))

    status = "preview"
    written = False
    reason = ""

    if apply:
        if profile_path.exists() and not force:
            status = "skipped"
            reason = "profile.json already exists (use --force to overwrite)"
        else:
            try:
                profile_path.parent.mkdir(parents=True, exist_ok=True)
                profile_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
                status = "written"
                written = True
            except Exception as exc:
                status = "error"
                reason = f"write failed: {exc}"

    result = {
        "status": status,
        "path": str(profile_path),
        "written": written,
        "reason": reason,
        "payload": payload,
    }

    if use_json:
        return _print_json(result)

    # Human-readable fallback
    sys.stdout.write(f"scan-profile: {status}\n")
    sys.stdout.write(f"  path:    {profile_path}\n")
    if reason:
        sys.stdout.write(f"  reason:  {reason}\n")
    sys.stdout.write("  preview:\n")
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0 if status != "error" else 1


# ---------------------------------------------------------------- quarantine (Fase E.5)

def _quarantine_list_impl(status: str = "pending", limit: int = 20) -> list[dict]:
    """Proxy to cognitive.quarantine_list, surfaced via Desktop bridge.

    Fase E.5 Desktop UI Guardian Proposals panel lists pending quarantine
    items with [Approve / Reject] actions. Desktop calls
    `nexo quarantine list --json` and gets the same shape as the MCP tool.
    """
    from cognitive import quarantine_list
    try:
        return quarantine_list(status=status, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]


def _quarantine_promote_impl(item_id: int) -> dict:
    from cognitive import quarantine_promote
    try:
        msg = quarantine_promote(item_id)
        return {"ok": not msg.startswith("ERROR"), "message": msg, "id": item_id}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc), "id": item_id}


def _quarantine_reject_impl(item_id: int, reason: str = "") -> dict:
    from cognitive import quarantine_reject
    try:
        msg = quarantine_reject(item_id, reason=reason)
        return {"ok": not msg.startswith("ERROR"), "message": msg, "id": item_id}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc), "id": item_id}


def cmd_quarantine_list(args) -> int:
    """CLI: `nexo quarantine list [--status X] [--limit N] [--json]`."""
    items = _quarantine_list_impl(
        status=getattr(args, "status", "pending") or "pending",
        limit=int(getattr(args, "limit", 20) or 20),
    )
    return _print_json({"items": items})


def cmd_quarantine_promote(args) -> int:
    """CLI: `nexo quarantine promote <id> [--json]`."""
    result = _quarantine_promote_impl(int(args.id))
    return _print_json(result)


def cmd_quarantine_reject(args) -> int:
    """CLI: `nexo quarantine reject <id> [--reason X] [--json]`."""
    result = _quarantine_reject_impl(
        int(args.id),
        reason=getattr(args, "reason", "") or "",
    )
    return _print_json(result)
