"""Entities plugin — people, services, URLs, recurring contacts."""
from db import create_entity, search_entities, list_entities, update_entity, delete_entity

def handle_entity_search(query: str, type: str = "") -> str:
    """Search entities by name or value. Optional type filter."""
    results = search_entities(query, type)
    if not results:
        return "Sin resultados."
    lines = []
    for e in results:
        notes = f" — {e['notes']}" if e.get("notes") else ""
        lines.append(f"  [{e['id']}] ({e['type']}) {e['name']}: {e['value']}{notes}")
    return f"ENTIDADES ({len(results)}):\n" + "\n".join(lines)

def handle_entity_create(name: str, type: str, value: str, notes: str = "") -> str:
    """Create a new entity."""
    eid = create_entity(name, type, value, notes)
    # KG hook
    try:
        from kg_populate import on_entity_create
        on_entity_create(eid, name, type)
    except Exception:
        pass
    return f"Entidad creada: [{eid}] {name} ({type})"

def handle_entity_update(id: int, name: str = "", type: str = "", value: str = "", notes: str = "") -> str:
    """Update an entity. Only non-empty fields are changed."""
    kwargs = {}
    if name: kwargs["name"] = name
    if type: kwargs["type"] = type
    if value: kwargs["value"] = value
    if notes: kwargs["notes"] = notes
    if not kwargs: return "Nada que actualizar."
    update_entity(id, **kwargs)
    return f"Entidad [{id}] actualizada."

def handle_entity_delete(id: int) -> str:
    """Delete an entity."""
    if not delete_entity(id):
        return f"ERROR: Entidad [{id}] no encontrada."
    return f"Entidad [{id}] eliminada."

def handle_entity_list(type: str = "") -> str:
    """List all entities, optionally filtered by type."""
    results = list_entities(type)
    if not results:
        return "Sin entidades."
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
            lines.append(f"    [{e['id']}] {e['name']}: {e['value']}{notes}")
    return f"ENTIDADES ({len(results)}):" + "\n".join(lines)

TOOLS = [
    (handle_entity_search, "nexo_entity_search", "Search entities by name, value, or type"),
    (handle_entity_create, "nexo_entity_create", "Create a new entity (person, service, URL)"),
    (handle_entity_update, "nexo_entity_update", "Update an entity's fields"),
    (handle_entity_delete, "nexo_entity_delete", "Delete an entity"),
    (handle_entity_list, "nexo_entity_list", "List all entities grouped by type"),
]
