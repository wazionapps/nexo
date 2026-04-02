from __future__ import annotations
"""NEXO DB — Entities module."""
import time
from db._core import get_db, _multi_word_like
from db._fts import fts_upsert

# ── Entities ──────────────────────────────────────────────────────

def create_entity(name: str, type: str, value: str, notes: str = "") -> int:
    """Create a new entity. Returns the entity ID."""
    conn = get_db()
    now = time.time()
    cursor = conn.execute(
        "INSERT INTO entities (name, type, value, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, type, value, notes, now, now)
    )
    conn.commit()
    eid = cursor.lastrowid
    fts_upsert("entity", str(eid), name, f"{name} {value} {notes}", type or "general", commit=False)
    return eid


def search_entities(query: str, type: str = "") -> list[dict]:
    """Search entities by name or value. Multi-word AND search."""
    conn = get_db()
    frag, params = _multi_word_like(query, ["name", "value"])
    if type:
        where = f"type = ? AND ({frag})"
        params.insert(0, type)
    else:
        where = frag
    rows = conn.execute(
        f"SELECT * FROM entities WHERE {where} ORDER BY updated_at DESC",
        params
    ).fetchall()
    return [dict(r) for r in rows]


def list_entities(type: str = "") -> list[dict]:
    """List all entities, optionally filtered by type."""
    conn = get_db()
    if type:
        rows = conn.execute(
            "SELECT * FROM entities WHERE type = ? ORDER BY name ASC",
            (type,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM entities ORDER BY type ASC, name ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def update_entity(id: int, **kwargs):
    """Update entity fields: name, type, value, notes."""
    conn = get_db()
    allowed = {"name", "type", "value", "notes"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
            return
    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE entities SET {set_clause} WHERE id = ?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM entities WHERE id = ?", (id,)).fetchone()
    if row:
        r = dict(row)
        fts_upsert("entity", str(id), r.get("name",""), f"{r.get('name','')} {r.get('value','')} {r.get('notes','')}", r.get("type","general"), commit=False)


def delete_entity(id: int) -> bool:
    """Delete an entity. Returns True if deleted, False if not found."""
    conn = get_db()
    result = conn.execute("DELETE FROM entities WHERE id = ?", (id,))
    conn.execute("DELETE FROM unified_search WHERE source = 'entity' AND source_id = ?", (str(id),))
    conn.commit()
    return result.rowcount > 0


# ── Preferences ───────────────────────────────────────────────────

def set_preference(key: str, value: str, category: str = "general"):
    """Set a preference (insert or update)."""
    conn = get_db()
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO preferences (key, value, category, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (key, value, category, now)
    )
    conn.commit()


def get_preference(key: str) -> dict | None:
    """Get a single preference by key."""
    conn = get_db()
    row = conn.execute("SELECT * FROM preferences WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def list_preferences(category: str = "") -> list[dict]:
    """List all preferences, optionally filtered by category."""
    conn = get_db()
    if category:
        rows = conn.execute(
            "SELECT * FROM preferences WHERE category = ? ORDER BY key ASC",
            (category,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM preferences ORDER BY category ASC, key ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_preference(key: str) -> bool:
    """Delete a preference. Returns True if deleted, False if not found."""
    conn = get_db()
    result = conn.execute("DELETE FROM preferences WHERE key = ?", (key,))
    conn.commit()
    return result.rowcount > 0


# ── Agents ────────────────────────────────────────────────────────

def create_agent(id: str, name: str, specialization: str, model: str = "sonnet",
                 tools: str = "", context_files: str = "", rules: str = "") -> dict:
    """Register a new agent. Uses INSERT OR REPLACE to allow re-registration."""
    conn = get_db()
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO agents (id, name, specialization, model, tools, context_files, rules, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (id, name, specialization, model, tools, context_files, rules, now, now)
    )
    conn.commit()
    return {"id": id, "name": name}


def get_agent(id: str) -> dict | None:
    """Get an agent by ID."""
    conn = get_db()
    row = conn.execute("SELECT * FROM agents WHERE id = ?", (id,)).fetchone()
    return dict(row) if row else None


def list_agents() -> list[dict]:
    """List all registered agents."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM agents ORDER BY name ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def update_agent(id: str, **kwargs):
    """Update agent fields: name, specialization, model, tools, context_files, rules."""
    conn = get_db()
    allowed = {"name", "specialization", "model", "tools", "context_files", "rules"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
            return
    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE agents SET {set_clause} WHERE id = ?", values)
    conn.commit()


def delete_agent(id: str) -> bool:
    """Delete an agent. Returns True if deleted, False if not found."""
    conn = get_db()
    result = conn.execute("DELETE FROM agents WHERE id = ?", (id,))
    conn.commit()
    return result.rowcount > 0


