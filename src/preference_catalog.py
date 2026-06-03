from __future__ import annotations

"""Unified preference catalog for agent-facing read/explain/set operations."""

import copy
import json
import re
from pathlib import Path
from typing import Any


AUTOMATION_ITEM_ES = {
    "priorities": (
        "Prioridades",
        "Incluye las cosas más importantes del día: lo urgente, lo atrasado y lo que conviene resolver primero.",
    ),
    "agenda": (
        "Agenda",
        "Añade citas, eventos o bloques de calendario que NEXO conozca para que sepas qué viene hoy.",
    ),
    "reminders": (
        "Recordatorios y tareas",
        "Muestra recordatorios y tareas pendientes que pueden requerir acción durante el día.",
    ),
    "followups": (
        "Seguimientos",
        "Incluye asuntos abiertos que NEXO debe empujar hasta que haya respuesta o decisión.",
    ),
    "decisions": (
        "Decisiones recientes",
        "Resume decisiones recientes guardadas para no perder cambios importantes de criterio.",
    ),
    "email_activity": (
        "Emails enviados recientes",
        "Añade emails enviados recientemente para dar contexto de conversaciones activas.",
    ),
    "blockers": (
        "Bloqueos y riesgos",
        "Señala bloqueos, riesgos o asuntos que pueden frenarte si no los atiendes.",
    ),
    "internal_refs": (
        "Referencias internas",
        "Incluye referencias internas útiles. Déjalo desactivado si prefieres un resumen más limpio.",
    ),
    "news": (
        "Noticias",
        "Añade titulares públicos. Si internet o la fuente de noticias falla, NEXO seguirá preparando el resumen.",
    ),
    "weather": (
        "Tiempo",
        "Añade el tiempo usando tu ubicación guardada en el perfil o en Desktop.",
    ),
    "length": (
        "Longitud",
        "Controla cuánto detalle quieres: corto, normal o más completo.",
    ),
    "tone": (
        "Tono",
        "Cambia cómo escribe el resumen: directo, cercano, ejecutivo o personal.",
    ),
    "format": (
        "Formato",
        "Elige la estructura visual del resumen: secciones, lista de puntos o texto narrativo.",
    ),
    "quiet_days": (
        "Días tranquilos",
        "Decide qué hacer cuando no hay novedades importantes.",
    ),
}


def _fold(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _label(value: Any, locale: str = "es") -> str:
    if isinstance(value, dict):
        return str(value.get(locale) or value.get("es") or value.get("en") or "").strip()
    return str(value or "").strip()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_file():
            payload = json.loads(path.read_text())
            if isinstance(payload, dict):
                return payload
    except Exception:
        pass
    return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _get_path(payload: dict[str, Any], dotted: str) -> Any:
    current: Any = payload
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _set_path(payload: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    next_payload = copy.deepcopy(payload)
    current = next_payload
    parts = dotted.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value
    return next_payload


def _coerce_value(value: Any, entry: dict[str, Any]) -> Any:
    kind = str(entry.get("type") or "").lower()
    if kind in {"boolean", "toggle"}:
        raw = str(value).strip().lower()
        return raw in {"1", "true", "yes", "on", "si", "sí", "activar", "enabled"}
    options = [str(option.get("value") if isinstance(option, dict) else option) for option in entry.get("options") or []]
    if options:
        raw = str(value).strip()
        if raw not in options:
            raise ValueError(f"Invalid value '{raw}'. Valid values: {', '.join(options)}")
        return raw
    return value


def _brain_paths() -> dict[str, Path]:
    import paths

    brain = paths.brain_dir()
    return {
        "calibration.json": brain / "calibration.json",
        "profile.json": brain / "profile.json",
    }


def _brain_schema_entries(*, include_values: bool, locale: str) -> list[dict[str, Any]]:
    from desktop_bridge import _schema_fields

    paths = _brain_paths()
    cache = {name: _read_json(path) for name, path in paths.items()}
    entries: list[dict[str, Any]] = []
    for field in _schema_fields():
        dotted = str(field.get("path") or field.get("writes") or "").strip()
        file_name = str(field.get("file") or "").strip()
        if not dotted or file_name not in paths:
            continue
        entry = {
            "id": dotted,
            "aliases": [field.get("id") or "", dotted.replace("user.", "assistant.")],
            "section": "assistant" if dotted.startswith("user.") else "profile",
            "label": _label(field.get("label") or field.get("prompt") or dotted, locale),
            "help": _label(field.get("hint") or "", locale),
            "type": str(field.get("type") or "text"),
            "options": field.get("options") or [],
            "writable": True,
            "storage": file_name,
            "path": dotted,
        }
        if include_values:
            entry["current_value"] = _get_path(cache[file_name], dotted)
        entries.append(entry)
    return entries


def _automation_entries(*, include_values: bool, locale: str) -> list[dict[str, Any]]:
    from automation_controls import get_core_automation_schedule_state
    from automation_preferences import get_automation_preferences

    contract = get_automation_preferences("morning-agent")
    schema = contract.get("schema") or {}
    values = ((contract.get("preferences") or {}).get("values") or {}) if include_values else {}
    entries: list[dict[str, Any]] = []
    for group in list(schema.get("groups") or []):
        for item in list(group.get("items") or []):
            item_id = str(item.get("id") or "").strip()
            if not item_id:
                continue
            localized = AUTOMATION_ITEM_ES.get(item_id) if locale == "es" else None
            label = (localized[0] if localized else str(item.get("label") or item_id))
            help_text = (localized[1] if localized else str(item.get("help") or item.get("disabled_reason") or ""))
            entry = {
                "id": f"automation.morning-agent.{item_id}",
                "aliases": [
                    f"morning-agent.{item_id}",
                    f"resumen.{item_id}",
                    str(item.get("label") or ""),
                    item_id.replace("_", " "),
                    label,
                ],
                "section": "automation.morning-agent",
                "group": str(group.get("id") or ""),
                "label": label,
                "help": help_text,
                "type": str(item.get("type") or "text"),
                "options": list(item.get("options") or []),
                "writable": not bool(item.get("disabled")),
                "storage": "personal_scripts.metadata.automation_preferences",
                "path": item_id,
            }
            if include_values:
                entry["current_value"] = values.get(item_id, item.get("default"))
            entries.append(entry)

    schedule_state = get_core_automation_schedule_state("morning-agent")
    schedule_entry = {
        "id": "automation.morning-agent.schedule",
        "aliases": ["resumen horario", "resumen frecuencia", "morning-agent schedule"],
        "section": "automation.morning-agent",
        "group": "schedule",
        "label": "Horario del resumen de la mañana",
        "help": "Hora y días en que se ejecuta el resumen. Acepta valores como 07:00 Mon-Fri o {\"daily_at\":\"07:00\",\"weekdays\":\"Tue,Sat\"}.",
        "type": "calendar",
        "writable": True,
        "storage": "schedule.json.core_automation_overrides",
        "path": "morning-agent.schedule",
    }
    if include_values:
        schedule_entry["current_value"] = {
            "label": schedule_state.get("effective_schedule_label"),
            "schedule": schedule_state.get("schedule"),
            "source": schedule_state.get("schedule_source"),
        }
    entries.append(schedule_entry)
    return entries


def _client_entries(*, include_values: bool) -> list[dict[str, Any]]:
    from client_preferences import load_client_preferences
    from resonance_map import TIERS, _load_user_default_resonance

    prefs = load_client_preferences()
    provider_runtime = prefs.get("provider_runtime") if isinstance(prefs.get("provider_runtime"), dict) else {}
    definitions = [
        ("client.default_terminal_client", "Cliente de chat por defecto", "codex o claude_code", ["codex", "claude_code"]),
        ("client.automation_enabled", "Automatizaciones activadas", "Activa o pausa rutinas de fondo.", []),
        ("client.automation_backend", "Motor de automatizaciones", "Backend usado por automatizaciones.", ["codex", "claude_code", "none"]),
        ("client.selected_chat_provider", "Proveedor de chat", "Proveedor seleccionado para conversaciones.", ["openai", "anthropic"]),
        ("client.default_resonance", "Nivel de razonamiento", "Nivel bajo/medio/alto/maximo usado por NEXO.", list(TIERS)),
    ]
    current = {
        "client.default_terminal_client": prefs.get("default_terminal_client"),
        "client.automation_enabled": prefs.get("automation_enabled"),
        "client.automation_backend": prefs.get("automation_backend"),
        "client.selected_chat_provider": provider_runtime.get("selected_chat_provider"),
        "client.default_resonance": _load_user_default_resonance() or prefs.get("default_resonance"),
    }
    entries = []
    for pref_id, label, help_text, options in definitions:
        entry = {
            "id": pref_id,
            "aliases": [pref_id.replace("client.", "")],
            "section": "client",
            "label": label,
            "help": help_text,
            "type": "boolean" if pref_id.endswith("automation_enabled") else "choice",
            "options": options,
            "writable": True,
            "storage": "schedule.json",
            "path": pref_id,
        }
        if include_values:
            entry["current_value"] = current.get(pref_id)
        entries.append(entry)
    return entries


def _desktop_app_entries() -> list[dict[str, Any]]:
    return [{
        "id": "desktop.app",
        "aliases": ["app settings", "preferencias de app", "desktop preferences"],
        "section": "desktop.app",
        "label": "Preferencias visuales y locales de Desktop",
        "help": "Estas opciones viven en Desktop y deben cambiarse desde Desktop para disparar guardado, mirrors y refrescos de UI.",
        "type": "catalog",
        "writable": False,
        "storage": "desktop.app-settings.json",
        "path": "app.*",
    }]


def build_preference_catalog(*, include_values: bool = False, query: str | None = None, locale: str = "es") -> dict[str, Any]:
    entries = [
        *_brain_schema_entries(include_values=include_values, locale=locale),
        *_automation_entries(include_values=include_values, locale=locale),
        *_client_entries(include_values=include_values),
        *_desktop_app_entries(),
    ]
    needle = _fold(query or "")
    if needle:
        entries = [
            entry for entry in entries
            if needle in _fold(" ".join([
                str(entry.get("id") or ""),
                str(entry.get("label") or ""),
                str(entry.get("help") or ""),
                " ".join(str(alias) for alias in entry.get("aliases") or []),
            ]))
        ]
    return {"ok": True, "count": len(entries), "preferences": entries}


def find_preference(id_or_alias: str, *, include_values: bool = True) -> dict[str, Any] | None:
    target = _fold(id_or_alias)
    if not target:
        return None
    catalog = build_preference_catalog(include_values=include_values)
    for entry in catalog["preferences"]:
        keys = [entry.get("id"), *(entry.get("aliases") or [])]
        if any(_fold(key) == target for key in keys):
            return entry
    matches = build_preference_catalog(include_values=include_values, query=id_or_alias)["preferences"]
    return matches[0] if len(matches) == 1 else None


def explain_preference(id_or_alias: str) -> dict[str, Any]:
    entry = find_preference(id_or_alias, include_values=True)
    if not entry:
        return {"ok": False, "error": f"Preference not found: {id_or_alias}"}
    return {"ok": True, "preference": entry}


def _set_brain_entry(entry: dict[str, Any], value: Any, *, dry_run: bool) -> dict[str, Any]:
    file_name = str(entry.get("storage") or "")
    dotted = str(entry.get("path") or "")
    paths = _brain_paths()
    path = paths.get(file_name)
    if not path:
        return {"ok": False, "error": f"Unsupported storage: {file_name}"}
    current = _read_json(path)
    coerced = _coerce_value(value, entry)
    next_payload = _set_path(current, dotted, coerced)
    if not dry_run:
        _write_json(path, next_payload)
    return {"ok": True, "dry_run": dry_run, "id": entry["id"], "value": coerced, "storage": file_name}


def _set_automation_entry(entry: dict[str, Any], value: Any, *, dry_run: bool) -> dict[str, Any]:
    from automation_preferences import get_automation_preferences, set_automation_preferences

    key = str(entry.get("path") or "")
    contract = get_automation_preferences("morning-agent")
    values = dict(((contract.get("preferences") or {}).get("values") or {}))
    coerced = _coerce_value(value, entry)
    values[key] = coerced
    if not dry_run:
        result = set_automation_preferences("morning-agent", {"values": values})
        if not result.get("ok"):
            return result
    return {"ok": True, "dry_run": dry_run, "id": entry["id"], "value": coerced}


def _parse_schedule_value(value: Any) -> tuple[str, Any]:
    if isinstance(value, dict):
        return str(value.get("daily_at") or value.get("time") or "").strip(), value.get("weekdays")
    text = str(value or "").strip()
    if text.startswith("{"):
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("Schedule JSON must be an object.")
        return _parse_schedule_value(payload)
    match = re.match(r"^(\d{1,2}:\d{2})(?:\s+(.+))?$", text)
    if not match:
        raise ValueError("Use HH:MM, optionally followed by days, e.g. 07:00 Mon-Fri.")
    return match.group(1), (match.group(2) or "")


def _set_schedule_entry(entry: dict[str, Any], value: Any, *, dry_run: bool) -> dict[str, Any]:
    from script_registry import set_automation_schedule

    daily_at, weekdays = _parse_schedule_value(value)
    if not daily_at:
        return {"ok": False, "error": "daily_at is required."}
    if dry_run:
        return {"ok": True, "dry_run": True, "id": entry["id"], "daily_at": daily_at, "weekdays": weekdays}
    result = set_automation_schedule("morning-agent", daily_at=daily_at, weekdays=weekdays)
    result["dry_run"] = False
    return result


def _set_client_entry(entry: dict[str, Any], value: Any, *, dry_run: bool) -> dict[str, Any]:
    from client_preferences import load_client_preferences, save_client_preferences

    pref_id = str(entry.get("id") or "")
    coerced = _coerce_value(value, entry)
    if dry_run:
        return {"ok": True, "dry_run": True, "id": pref_id, "value": coerced}
    if pref_id == "client.default_terminal_client":
        save_client_preferences(default_terminal_client=str(coerced))
    elif pref_id == "client.automation_enabled":
        save_client_preferences(automation_enabled=bool(coerced), automation_user_override=True)
    elif pref_id == "client.automation_backend":
        save_client_preferences(automation_backend=str(coerced), automation_user_override=True)
    elif pref_id == "client.selected_chat_provider":
        save_client_preferences(selected_chat_provider=str(coerced))
    elif pref_id == "client.default_resonance":
        save_client_preferences(default_resonance=str(coerced))
        try:
            from cli import _write_calibration_default_resonance

            _write_calibration_default_resonance(str(coerced))
        except Exception:
            pass
    else:
        return {"ok": False, "error": f"Unsupported client preference: {pref_id}"}
    return {"ok": True, "dry_run": False, "id": pref_id, "value": coerced, "preferences": load_client_preferences()}


def set_preference(id_or_alias: str, value: Any, *, dry_run: bool = False) -> dict[str, Any]:
    entry = find_preference(id_or_alias, include_values=False)
    if not entry:
        return {"ok": False, "error": f"Preference not found: {id_or_alias}"}
    if not entry.get("writable"):
        return {"ok": False, "error": "This preference is read-only from Brain; use Desktop for app.* preferences.", "preference": entry}
    pref_id = str(entry.get("id") or "")
    if pref_id == "automation.morning-agent.schedule":
        return _set_schedule_entry(entry, value, dry_run=dry_run)
    if pref_id.startswith("automation.morning-agent."):
        return _set_automation_entry(entry, value, dry_run=dry_run)
    if pref_id.startswith("client."):
        return _set_client_entry(entry, value, dry_run=dry_run)
    return _set_brain_entry(entry, value, dry_run=dry_run)


__all__ = [
    "build_preference_catalog",
    "explain_preference",
    "find_preference",
    "set_preference",
]
