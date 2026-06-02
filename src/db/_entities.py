from __future__ import annotations
"""NEXO DB — Entities module."""
import json
import time
from db._core import get_db, _multi_word_like
from db._fts import fts_upsert

# ── Entities ──────────────────────────────────────────────────────


def _json_text(value, default):
    if value in (None, ""):
        value = default
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return json.dumps(default, ensure_ascii=False, sort_keys=True)


def _entity_columns(conn) -> set[str]:
    try:
        return {str(row["name"]) for row in conn.execute("PRAGMA table_info(entities)").fetchall()}
    except Exception:
        return set()


def _entity_search_columns(conn) -> list[str]:
    columns = ["name", "value"]
    available = _entity_columns(conn)
    if "aliases" in available:
        columns.append("aliases")
    if "metadata" in available:
        columns.append("metadata")
    return columns


def _entity_fts_content(row: dict) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("name", "value", "notes", "aliases", "metadata", "access_mode")
    )


def create_entity(
    name: str,
    type: str,
    value: str,
    notes: str = "",
    aliases=None,
    metadata=None,
    source: str = "manual",
    confidence: float = 1.0,
    access_mode: str = "unknown",
) -> int:
    """Create a new entity. Returns the entity ID."""
    conn = get_db()
    now = time.time()
    row = {
        "name": name,
        "type": type,
        "value": value,
        "notes": notes,
        "aliases": _json_text(aliases, []),
        "metadata": _json_text(metadata, {}),
        "source": source or "manual",
        "confidence": float(confidence if confidence is not None else 1.0),
        "access_mode": access_mode or "unknown",
        "created_at": now,
        "updated_at": now,
    }
    available = _entity_columns(conn)
    columns = [
        key
        for key in (
            "name", "type", "value", "notes", "aliases", "metadata",
            "source", "confidence", "access_mode", "created_at", "updated_at",
        )
        if key in available
    ]
    if not columns:
        columns = ["name", "type", "value", "notes", "created_at", "updated_at"]
    cursor = conn.execute(
        f"INSERT INTO entities ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(row[key] for key in columns),
    )
    conn.commit()
    eid = cursor.lastrowid
    fts_upsert("entity", str(eid), name, _entity_fts_content(row), type or "general", commit=False)
    return eid


def search_entities(query: str, type: str = "") -> list[dict]:
    """Search entities by name or value. Multi-word AND search."""
    conn = get_db()
    frag, params = _multi_word_like(query, _entity_search_columns(conn))
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
    """Update entity fields, including extended alias/metadata/access fields."""
    conn = get_db()
    allowed = {
        "name", "type", "value", "notes", "aliases", "metadata",
        "source", "confidence", "access_mode",
    }
    available = _entity_columns(conn)
    allowed &= available
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
            return
    if "aliases" in updates:
        updates["aliases"] = _json_text(updates["aliases"], [])
    if "metadata" in updates:
        updates["metadata"] = _json_text(updates["metadata"], {})
    if "confidence" in updates:
        updates["confidence"] = float(updates["confidence"] if updates["confidence"] is not None else 1.0)
    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE entities SET {set_clause} WHERE id = ?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM entities WHERE id = ?", (id,)).fetchone()
    if row:
        r = dict(row)
        fts_upsert(
            "entity",
            str(id),
            r.get("name", ""),
            _entity_fts_content(r),
            r.get("type", "general"),
            commit=False,
        )


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

def create_agent(id: str, name: str, specialization: str, model: str = "",
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
