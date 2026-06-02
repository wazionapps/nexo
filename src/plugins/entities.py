"""Entities plugin — people, services, URLs, recurring contacts."""
import json

from db import create_entity, search_entities, list_entities, update_entity, delete_entity


def _json_arg(value: str, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        if isinstance(default, list):
            return [part.strip() for part in str(value).split(",") if part.strip()]
        return default

def handle_entity_search(query: str, type: str = "") -> str:
    """Search entities by name or value. Optional type filter."""
    results = search_entities(query, type)
    if not results:
        return "No results."
    lines = []
    for e in results:
        notes = f" — {e['notes']}" if e.get("notes") else ""
        access = f" [{e.get('access_mode')}]" if e.get("access_mode") and e.get("access_mode") != "unknown" else ""
        lines.append(f"  [{e['id']}] ({e['type']}) {e['name']}{access}: {e['value']}{notes}")
    return f"ENTIDADES ({len(results)}):\n" + "\n".join(lines)

def handle_entity_create(
    name: str,
    type: str,
    value: str,
    notes: str = "",
    aliases: str = "[]",
    metadata: str = "{}",
    source: str = "manual",
    confidence: float = 1.0,
    access_mode: str = "unknown",
) -> str:
    """Create a new entity."""
    eid = create_entity(
        name,
        type,
        value,
        notes,
        aliases=_json_arg(aliases, []),
        metadata=_json_arg(metadata, {}),
        source=source,
        confidence=confidence,
        access_mode=access_mode,
    )
    # KG hook
    try:
        from kg_populate import on_entity_create
        on_entity_create(eid, name, type)
    except Exception:
        pass
    return f"Entity created: [{eid}] {name} ({type})"

def handle_entity_update(
    id: int,
    name: str = "",
    type: str = "",
    value: str = "",
    notes: str = "",
    aliases: str = "",
    metadata: str = "",
    source: str = "",
    confidence: str = "",
    access_mode: str = "",
) -> str:
    """Update an entity. Only non-empty fields are changed."""
    kwargs = {}
    if name: kwargs["name"] = name
    if type: kwargs["type"] = type
    if value: kwargs["value"] = value
    if notes: kwargs["notes"] = notes
    if aliases: kwargs["aliases"] = _json_arg(aliases, [])
    if metadata: kwargs["metadata"] = _json_arg(metadata, {})
    if source: kwargs["source"] = source
    if confidence:
        try:
            kwargs["confidence"] = float(confidence)
        except Exception:
            return "ERROR: confidence must be a number between 0 and 1."
    if access_mode: kwargs["access_mode"] = access_mode
    if not kwargs: return "Nothing to update."
    update_entity(id, **kwargs)
    return f"Entity [{id}] updated."

def handle_entity_delete(id: int) -> str:
    """Delete an entity."""
    if not delete_entity(id):
        return f"ERROR: Entity [{id}] not found."
    return f"Entity [{id}] deleted."

def handle_entity_list(type: str = "") -> str:
    """List all entities, optionally filtered by type."""
    results = list_entities(type)
    if not results:
        return "No entities."
    grouped = {}
    for e in results:
        t = e["type"]
        if t not in grouped: grouped[t] = []
        grouped[t].append(e)
    lines = []
    for t, entities in grouped.items():
        lines.append(f"\n  [{t.upper()}]")
        for e in entities:
            notes = f" — {e['notes']}" if e.get("notes") else ""
            access = f" [{e.get('access_mode')}]" if e.get("access_mode") and e.get("access_mode") != "unknown" else ""
            lines.append(f"    [{e['id']}] {e['name']}{access}: {e['value']}{notes}")
    return f"ENTIDADES ({len(results)}):" + "\n".join(lines)

TOOLS = [
    (handle_entity_search, "nexo_entity_search", "Search entities by name, value, or type"),
    (handle_entity_create, "nexo_entity_create", "Create a new entity (person, service, URL)"),
    (handle_entity_update, "nexo_entity_update", "Update an entity's fields"),
    (handle_entity_delete, "nexo_entity_delete", "Delete an entity"),
    (handle_entity_list, "nexo_entity_list", "List all entities grouped by type"),
]
