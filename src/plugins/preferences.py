"""Preferences plugin — learned behavior patterns and workflow rules."""
from db import set_preference, get_preference, list_preferences, delete_preference

def handle_preference_get(key: str) -> str:
    """Get a specific preference by key."""
    p = get_preference(key)
    if not p: return f"Preference '{key}' not found."
    return f"{p['key']} = {p['value']} (cat: {p['category']})"

def handle_preference_set(key: str, value: str, category: str = "general") -> str:
    """Set a preference (creates or updates)."""
    set_preference(key, value, category)
    try:
        import cognitive
        cognitive.ingest_to_ltm(f"{key}: {value}", "preference", key, key, "")
    except Exception:
        pass
    return f"Preference '{key}' = '{value}' ({category})"

def handle_preference_list(category: str = "") -> str:
    """List all preferences, optionally filtered by category."""
    prefs = list_preferences(category)
    if not prefs: return "No preferences."
    grouped = {}
    for p in prefs:
        c = p["category"]
        if c not in grouped: grouped[c] = []
        grouped[c].append(p)
    lines = ["PREFERENCES:"]
    for c, items in grouped.items():
        lines.append(f"\n  [{c.upper()}]")
        for p in items:
            lines.append(f"    {p['key']} = {p['value']}")
    return "\n".join(lines)

def handle_preference_delete(key: str) -> str:
    """Delete a preference."""
    if not delete_preference(key):
        return f"ERROR: Preference '{key}' not found."
    return f"Preference '{key}' deleted."

TOOLS = [
    (handle_preference_get, "nexo_preference_get", "Get a specific preference value"),
    (handle_preference_set, "nexo_preference_set", "Set a preference (creates or updates)"),
    (handle_preference_list, "nexo_preference_list", "List all preferences grouped by category"),
    (handle_preference_delete, "nexo_preference_delete", "Delete a preference"),
]
