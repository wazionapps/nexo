"""Canonical Guardian runtime surfaces shared with Desktop.

Desktop's JS enforcement engine cannot query the live SQLite entity registry
directly, so Brain exports a small JSON snapshot with the rule-relevant
datasets. This avoids drifting back to legacy manual lists in guardian.json
once the DB already knows the canonical hosts / projects / legacy paths.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import paths

SNAPSHOT_FILENAME = "guardian-runtime-surfaces.json"
SCHEMA_ID = "guardian-runtime-surfaces-v1"
_RUNTIME_SURFACE_ENTITY_TYPES = frozenset({
    "host",
    "destructive_command",
    "project",
    "legacy_path",
    "vhost_mapping",
    "db",
})


def _resolve_home(nexo_home: str | Path | None = None) -> Path:
    if nexo_home is None:
        return paths.home()
    return Path(nexo_home).expanduser()


def _brain_dir(nexo_home: str | Path | None = None) -> Path:
    home = _resolve_home(nexo_home)
    new = home / "personal" / "brain"
    legacy = home / "brain"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def guardian_runtime_surfaces_path(nexo_home: str | Path | None = None) -> Path:
    return _brain_dir(nexo_home) / SNAPSHOT_FILENAME


def _preset_entities_candidates(nexo_home: str | Path | None = None) -> list[Path]:
    brain_dir = _brain_dir(nexo_home)
    home = _resolve_home(nexo_home)
    return [
      brain_dir / "presets" / "entities_universal.json",
      home / "brain" / "presets" / "entities_universal.json",
    ]


def _coerce_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw or "{}")
        except Exception:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _entity_record(name: str, entity_type: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(name or "").strip(),
        "type": str(entity_type or "").strip(),
        "metadata": metadata if isinstance(metadata, dict) else {},
    }


def _is_runtime_surface_entity(entity: dict[str, Any]) -> bool:
    entity_type = str(entity.get("type") or "").strip()
    if entity_type not in _RUNTIME_SURFACE_ENTITY_TYPES:
        return False
    if entity_type != "db":
        return True
    metadata = _coerce_metadata(entity.get("metadata") or {})
    return str(metadata.get("env") or "").strip().lower() == "production"


def _load_entities_from_db() -> list[dict[str, Any]]:
    try:
        from db import list_entities  # type: ignore
    except Exception:
        return []
    try:
        rows = list_entities()
    except Exception:
        return []
    entities: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        entity_type = str(row.get("type") or "").strip()
        if not name or not entity_type:
            continue
        entity = _entity_record(name, entity_type, _coerce_metadata(row.get("value") or {}))
        if _is_runtime_surface_entity(entity):
            entities.append(entity)
    return entities


def _load_entities_from_preset(nexo_home: str | Path | None = None) -> list[dict[str, Any]]:
    for candidate in _preset_entities_candidates(nexo_home):
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text())
        except Exception:
            continue
        raw_entities = payload.get("entities") if isinstance(payload, dict) else None
        if not isinstance(raw_entities, list):
            continue
        entities: list[dict[str, Any]] = []
        for entry in raw_entities:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            entity_type = str(entry.get("type") or "").strip()
            if not name or not entity_type:
                continue
            entity = _entity_record(name, entity_type, _coerce_metadata(entry.get("metadata") or {}))
            if _is_runtime_surface_entity(entity):
                entities.append(entity)
        return entities
    return []


def _load_entities(nexo_home: str | Path | None = None) -> tuple[list[dict[str, Any]], str]:
    db_entities = _load_entities_from_db()
    if db_entities:
        return db_entities, "db"
    preset_entities = _load_entities_from_preset(nexo_home)
    if preset_entities:
        return preset_entities, "preset_fallback"
    return [], "empty"


def build_guardian_runtime_surfaces(nexo_home: str | Path | None = None) -> dict[str, Any]:
    entities, source = _load_entities(nexo_home)
    known_hosts: set[str] = set()
    read_only_hosts: set[str] = set()
    destructive_patterns: list[str] = []
    projects: list[dict[str, Any]] = []
    legacy_mappings: list[dict[str, str]] = []
    vhost_mappings: list[dict[str, Any]] = []
    db_production_markers: list[str] = []
    all_entities_flat: list[dict[str, str]] = []

    for entity in entities:
        name = str(entity.get("name") or "").strip()
        entity_type = str(entity.get("type") or "").strip()
        metadata = _coerce_metadata(entity.get("metadata") or {})
        if not name or not entity_type:
            continue
        all_entities_flat.append({"name": name, "type": entity_type})

        if entity_type == "host":
            aliases = [str(alias or "").strip() for alias in metadata.get("aliases") or []]
            for candidate in [name] + aliases:
                if candidate:
                    known_hosts.add(candidate.lower())
            if str(metadata.get("access_mode") or "").strip().lower() == "read_only":
                for candidate in [name] + aliases:
                    if candidate:
                        read_only_hosts.add(candidate)
            continue

        if entity_type == "destructive_command":
            pattern = str(metadata.get("pattern") or "").strip()
            if pattern:
                destructive_patterns.append(pattern)
            continue

        if entity_type == "project":
            aliases = [str(alias or "").strip() for alias in metadata.get("aliases") or [] if str(alias or "").strip()]
            path_patterns = [
                str(pattern or "").strip()
                for pattern in metadata.get("path_patterns") or []
                if str(pattern or "").strip()
            ]
            projects.append({
                "name": name,
                "aliases": aliases,
                "require_grep": bool(metadata.get("require_grep")),
                "path_patterns": path_patterns,
                "local_path": str(metadata.get("local_path") or "").strip(),
                "deploy": metadata.get("deploy") if isinstance(metadata.get("deploy"), dict) else {},
            })
            continue

        if entity_type == "legacy_path":
            old = str(metadata.get("old") or "").strip()
            canonical = str(metadata.get("canonical") or "").strip()
            if old and canonical:
                legacy_mappings.append({"old": old, "canonical": canonical})
            continue

        if entity_type == "vhost_mapping":
            vhost_mappings.append({"name": name, "metadata": metadata})
            continue

        if entity_type == "db" and str(metadata.get("env") or "").strip().lower() == "production":
            for key in ("host", "hostname", "uri", "connection_string"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    db_production_markers.append(value.strip())

    return {
        "schema": SCHEMA_ID,
        "generated_at": int(time.time()),
        "source": source,
        "entity_count": len(entities),
        "entities": entities,
        "known_hosts": sorted(known_hosts),
        "read_only_hosts": sorted(read_only_hosts),
        "destructive_patterns": destructive_patterns,
        "projects": projects,
        "legacy_mappings": legacy_mappings,
        "vhost_mappings": vhost_mappings,
        "db_production_markers": db_production_markers,
        "all_entities_flat": all_entities_flat,
    }


def write_guardian_runtime_surfaces(
    *,
    nexo_home: str | Path | None = None,
    target_path: str | Path | None = None,
) -> dict[str, Any]:
    payload = build_guardian_runtime_surfaces(nexo_home=nexo_home)
    path = Path(target_path).expanduser() if target_path else guardian_runtime_surfaces_path(nexo_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return {
        "ok": True,
        "path": str(path),
        "entity_count": int(payload.get("entity_count") or 0),
        "source": str(payload.get("source") or ""),
    }
