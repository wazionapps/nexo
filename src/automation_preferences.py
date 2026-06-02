"""Structured operator preferences for product automations."""

from __future__ import annotations

import copy
import json
import unicodedata
from pathlib import Path
from typing import Any


AUTOMATION_PREFERENCES_METADATA_KEY = "automation_preferences"
SUPPORTED_AUTOMATIONS = {"morning-agent"}


MORNING_AGENT_SCHEMA: dict[str, Any] = {
    "schema_version": 1,
    "automation": "morning-agent",
    "title": "Morning briefing content",
    "groups": [
        {
            "id": "content",
            "label": "Content",
            "items": [
                {"id": "priorities", "type": "boolean", "label": "Priorities", "default": True},
                {"id": "agenda", "type": "boolean", "label": "Agenda", "default": True},
                {"id": "reminders", "type": "boolean", "label": "Reminders and tasks", "default": True},
                {"id": "followups", "type": "boolean", "label": "Follow-ups", "default": True},
                {"id": "decisions", "type": "boolean", "label": "Recent decisions", "default": True},
                {"id": "email_activity", "type": "boolean", "label": "Recent sent email", "default": True},
                {"id": "blockers", "type": "boolean", "label": "Blockers and risks", "default": True},
                {"id": "internal_refs", "type": "boolean", "label": "Internal references", "default": False},
                {
                    "id": "news",
                    "type": "boolean",
                    "label": "News",
                    "default": False,
                    "disabled": True,
                    "disabled_reason": "No verified news source is connected yet.",
                },
                {
                    "id": "weather",
                    "type": "boolean",
                    "label": "Weather",
                    "default": False,
                    "disabled": True,
                    "disabled_reason": "No verified weather source is connected yet.",
                },
            ],
        },
        {
            "id": "style",
            "label": "Style",
            "items": [
                {
                    "id": "length",
                    "type": "choice",
                    "label": "Length",
                    "default": "normal",
                    "options": ["short", "normal", "detailed"],
                },
                {
                    "id": "tone",
                    "type": "choice",
                    "label": "Tone",
                    "default": "direct",
                    "options": ["direct", "warm", "executive", "personal"],
                },
                {
                    "id": "format",
                    "type": "choice",
                    "label": "Format",
                    "default": "sections",
                    "options": ["sections", "bullets", "narrative"],
                },
            ],
        },
        {
            "id": "delivery",
            "label": "Delivery",
            "items": [
                {
                    "id": "quiet_days",
                    "type": "choice",
                    "label": "Quiet days",
                    "default": "summary_if_anything_important",
                    "options": ["always_send", "summary_if_anything_important", "skip_if_empty"],
                },
                {
                    "id": "audience",
                    "type": "choice",
                    "label": "User type",
                    "default": "operator",
                    "options": ["operator", "founder", "student", "sales", "technical", "personal"],
                },
            ],
        },
    ],
}


def _schema_for(name: str) -> dict[str, Any] | None:
    clean = normalize_automation_name(name)
    if clean == "morning-agent":
        return copy.deepcopy(MORNING_AGENT_SCHEMA)
    return None


def normalize_automation_name(name: str) -> str:
    return str(name or "").strip().lower().replace("_", "-")


def supports_automation_preferences(name: str) -> bool:
    return normalize_automation_name(name) in SUPPORTED_AUTOMATIONS


def get_automation_preference_schema(name: str) -> dict[str, Any]:
    schema = _schema_for(name)
    if not schema:
        return {
            "schema_version": 1,
            "automation": normalize_automation_name(name),
            "title": "Automation content",
            "groups": [],
        }
    return schema


def _iter_schema_items(schema: dict[str, Any]):
    for group in list(schema.get("groups") or []):
        for item in list(group.get("items") or []):
            if isinstance(item, dict) and item.get("id"):
                yield item


def default_automation_preferences(name: str) -> dict[str, Any]:
    schema = get_automation_preference_schema(name)
    values: dict[str, Any] = {}
    for item in _iter_schema_items(schema):
        values[str(item["id"])] = copy.deepcopy(item.get("default"))
    return {
        "schema_version": int(schema.get("schema_version") or 1),
        "automation": normalize_automation_name(name),
        "values": values,
    }


def validate_automation_preferences(name: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    schema = get_automation_preference_schema(name)
    defaults = default_automation_preferences(name)
    source_values = {}
    if isinstance(payload, dict):
        if isinstance(payload.get("values"), dict):
            source_values = payload.get("values") or {}
        else:
            source_values = payload
    values = dict(defaults["values"])
    warnings: list[str] = []
    for item in _iter_schema_items(schema):
        key = str(item["id"])
        if key not in source_values:
            continue
        if item.get("disabled"):
            values[key] = copy.deepcopy(item.get("default"))
            warnings.append(f"{key}: disabled")
            continue
        kind = str(item.get("type") or "text")
        raw_value = source_values.get(key)
        if kind == "boolean":
            values[key] = bool(raw_value)
        elif kind == "choice":
            options = [str(v) for v in list(item.get("options") or [])]
            clean = str(raw_value or "").strip()
            if clean in options:
                values[key] = clean
            else:
                warnings.append(f"{key}: invalid choice")
        elif kind == "number":
            try:
                values[key] = int(raw_value)
            except Exception:
                warnings.append(f"{key}: invalid number")
        else:
            values[key] = str(raw_value or "").strip()[:1000]
    return {
        "schema_version": int(schema.get("schema_version") or 1),
        "automation": normalize_automation_name(name),
        "values": values,
        "warnings": warnings,
    }


def _script_row_for(name_or_path: str) -> tuple[dict, dict] | tuple[None, None]:
    from db import init_db
    from db._personal_scripts import get_personal_script
    from script_registry import resolve_script, sync_personal_scripts

    init_db()
    sync_personal_scripts()
    script = get_personal_script(name_or_path, include_core=True) or resolve_script(name_or_path)
    if not script and normalize_automation_name(name_or_path) == "morning-agent":
        script_path = Path(__file__).resolve().parent / "scripts" / "nexo-morning-agent.py"
        script = {
            "name": "morning-agent",
            "path": str(script_path),
            "description": "Generate and send the operator's daily morning briefing email.",
            "runtime": "python",
            "core": True,
            "metadata": {},
            "origin": "core",
        }
    if not script:
        return None, None
    existing = get_personal_script(script.get("path", ""), include_core=True) or script
    return script, existing


def get_automation_preferences(name_or_path: str) -> dict[str, Any]:
    clean_name = normalize_automation_name(name_or_path)
    script, existing = _script_row_for(name_or_path)
    if script:
        clean_name = normalize_automation_name(script.get("name") or clean_name)
    metadata = (existing or {}).get("metadata") if isinstance((existing or {}).get("metadata"), dict) else {}
    stored = metadata.get(AUTOMATION_PREFERENCES_METADATA_KEY) if isinstance(metadata, dict) else {}
    validated = validate_automation_preferences(clean_name, stored if isinstance(stored, dict) else {})
    return {
        "ok": True,
        "name": clean_name,
        "schema": get_automation_preference_schema(clean_name),
        "preferences": validated,
        "supports_automation_preferences": supports_automation_preferences(clean_name),
    }


def set_automation_preferences(name_or_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    from db._personal_scripts import upsert_personal_script

    script, existing = _script_row_for(name_or_path)
    if not script:
        return {"ok": False, "error": f"Automation not found: {name_or_path}"}
    clean_name = normalize_automation_name(script.get("name") or name_or_path)
    if not supports_automation_preferences(clean_name):
        return {"ok": False, "error": "This automation does not support structured preferences."}
    validated = validate_automation_preferences(clean_name, payload)
    metadata = dict((existing or script).get("metadata") or {})
    metadata[AUTOMATION_PREFERENCES_METADATA_KEY] = {
        "schema_version": validated["schema_version"],
        "values": validated["values"],
    }
    script_origin = "core" if (bool(script.get("core")) or str(script.get("origin") or "") == "core") else "user"
    upsert_personal_script(
        name=script.get("name", clean_name),
        path=script.get("path", ""),
        description=script.get("description", ""),
        runtime=script.get("runtime", "unknown"),
        metadata=metadata,
        created_by="nexo-core" if script_origin == "core" else "manual",
        source="core-toggle" if script_origin == "core" else "filesystem",
        origin=script_origin,
        enabled=bool((existing or script).get("enabled", True)),
        has_inline_metadata=bool(script.get("metadata")),
    )
    return {
        "ok": True,
        "name": clean_name,
        "preferences": validated,
        "supports_automation_preferences": True,
    }


def search_automation_preference_schema(name: str, query: str) -> list[dict[str, Any]]:
    clean_query = _fold_text(query)
    if not clean_query:
        return []
    matches: list[dict[str, Any]] = []
    for group in list(get_automation_preference_schema(name).get("groups") or []):
        for item in list(group.get("items") or []):
            text = " ".join([
                str(item.get("id") or ""),
                str(item.get("label") or ""),
                str(item.get("disabled_reason") or ""),
                str(group.get("label") or ""),
            ])
            if clean_query in _fold_text(text):
                matches.append({"group": group.get("id"), **item})
    return matches


def _fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    asciiish = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return asciiish.casefold()


def format_automation_preferences_prompt_block(name_or_path: str) -> str:
    result = get_automation_preferences(name_or_path)
    if not result.get("supports_automation_preferences"):
        return ""
    prefs = result.get("preferences") or {}
    values = prefs.get("values") if isinstance(prefs, dict) else {}
    if not isinstance(values, dict):
        values = {}
    compact = json.dumps(values, ensure_ascii=False, sort_keys=True)
    return (
        "\n== STRUCTURED CONTENT PREFERENCES FOR THIS AUTOMATION ==\n"
        f"{compact}\n"
        "Use these preferences to decide what to include, omit, and emphasize. "
        "Disabled/unavailable data sources must not be invented.\n"
    )


__all__ = [
    "AUTOMATION_PREFERENCES_METADATA_KEY",
    "default_automation_preferences",
    "format_automation_preferences_prompt_block",
    "get_automation_preference_schema",
    "get_automation_preferences",
    "normalize_automation_name",
    "search_automation_preference_schema",
    "set_automation_preferences",
    "supports_automation_preferences",
    "validate_automation_preferences",
]
