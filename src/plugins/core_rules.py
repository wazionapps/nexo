"""Core Rules plugin — query and manage versioned behavioral rules."""

import json
import os


def _get_db():
    from db import get_db
    return get_db()


def _seed_if_empty():
    """Seed rules from JSON if table is empty (first run after migration)."""
    conn = _get_db()
    count = conn.execute("SELECT COUNT(*) FROM core_rules WHERE is_active = 1").fetchone()[0]
    if count > 0:
        return

    rules_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "rules", "core-rules.json")
    if not os.path.exists(rules_file):
        return

    with open(rules_file) as f:
        data = json.load(f)

    version = data["_meta"]["version"]
    for cat_key, cat in data["categories"].items():
        for rule in cat["rules"]:
            conn.execute(
                """INSERT OR REPLACE INTO core_rules (id, category, rule, why, importance, type, added_in)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (rule["id"], cat_key, rule["rule"], rule["why"],
                 rule["importance"], rule["type"], rule.get("added_in", version))
            )

    conn.execute("UPDATE core_rules_version SET version = ?, updated_at = datetime('now') WHERE id = 1", (version,))
    conn.commit()


def handle_rules_check(area: str = "", importance_min: int = 0) -> str:
    """Get applicable core rules for a given area or action.

    Returns BLOCKING rules that must be followed and ADVISORY rules as guidance.
    Call this before taking any significant action.

    Args:
        area: Area of work — 'code', 'delegation', 'communication', 'memory', or empty for all.
              Maps to categories: code→execution+integrity, delegation→delegation, etc.
        importance_min: Minimum importance level (1-5, default 0 = all rules)
    """
    _seed_if_empty()
    conn = _get_db()

    area_to_categories = {
        "code": ("integrity", "execution"),
        "edit": ("integrity", "execution"),
        "delegation": ("delegation",),
        "delegate": ("delegation",),
        "subagent": ("delegation",),
        "communication": ("communication",),
        "respond": ("communication",),
        "memory": ("memory",),
        "learn": ("memory",),
        "proactivity": ("proactivity",),
        "protect": ("proactivity",),
    }

    where = "WHERE is_active = 1"
    params = []

    if area and area.lower() in area_to_categories:
        cats = area_to_categories[area.lower()]
        placeholders = ",".join("?" * len(cats))
        where += f" AND category IN ({placeholders})"
        params.extend(cats)

    if importance_min > 0:
        where += " AND importance >= ?"
        params.append(importance_min)

    rows = conn.execute(
        f"SELECT id, category, rule, why, importance, type FROM core_rules {where} ORDER BY importance DESC, category, id",
        params
    ).fetchall()

    if not rows:
        return "No rules found for this area."

    # Get version
    ver = conn.execute("SELECT version FROM core_rules_version WHERE id = 1").fetchone()
    version = ver[0] if ver else "unknown"

    blocking = [r for r in rows if r["type"] == "blocking"]
    advisory = [r for r in rows if r["type"] == "advisory"]

    lines = [f"CORE RULES (v{version}) — {len(blocking)} BLOCKING, {len(advisory)} ADVISORY"]
    if area:
        lines[0] += f" [area: {area}]"
    lines.append("")

    if blocking:
        lines.append("## BLOCKING (must follow)")
        for r in blocking:
            lines.append(f"  {r['id']}. {r['rule']}")
            lines.append(f"     Why: {r['why']}")
        lines.append("")

    if advisory:
        lines.append("## ADVISORY (recommended)")
        for r in advisory:
            lines.append(f"  {r['id']}. {r['rule']}")
            lines.append(f"     Why: {r['why']}")

    return "\n".join(lines)


def handle_rules_list() -> str:
    """List all core rules with their status, grouped by category."""
    _seed_if_empty()
    conn = _get_db()

    ver = conn.execute("SELECT version FROM core_rules_version WHERE id = 1").fetchone()
    version = ver[0] if ver else "unknown"

    rows = conn.execute(
        "SELECT id, category, rule, importance, type, is_active, added_in, removed_in FROM core_rules ORDER BY category, id"
    ).fetchall()

    lines = [f"CORE RULES v{version} — {len([r for r in rows if r['is_active']])} active, {len([r for r in rows if not r['is_active']])} removed", ""]

    current_cat = None
    for r in rows:
        if r["category"] != current_cat:
            current_cat = r["category"]
            lines.append(f"### {current_cat.upper()}")

        status = "✓" if r["is_active"] else "✗"
        tag = "BLOCK" if r["type"] == "blocking" else "ADVSR"
        lines.append(f"  [{status}] {r['id']} [{tag}] imp={r['importance']} — {r['rule']}")
        if r["removed_in"]:
            lines.append(f"       Removed in v{r['removed_in']}")

    return "\n".join(lines)


def handle_rules_migrate(dry_run: bool = False) -> str:
    """Sync core rules from the JSON definition file to the database.

    Adds new rules, marks removed ones as inactive. Safe to run multiple times.

    Args:
        dry_run: If True, show what would change without applying
    """
    conn = _get_db()
    rules_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "rules", "core-rules.json")
    if not os.path.exists(rules_file):
        return "ERROR: core-rules.json not found"

    with open(rules_file) as f:
        data = json.load(f)

    new_version = data["_meta"]["version"]
    ver = conn.execute("SELECT version FROM core_rules_version WHERE id = 1").fetchone()
    current_version = ver[0] if ver else "0.0.0"

    # Collect all rule IDs from JSON
    json_ids = set()
    json_rules = {}
    for cat_key, cat in data["categories"].items():
        for rule in cat["rules"]:
            json_ids.add(rule["id"])
            json_rules[rule["id"]] = {**rule, "category": cat_key}

    # Collect active IDs from DB
    db_ids = set()
    for r in conn.execute("SELECT id FROM core_rules WHERE is_active = 1").fetchall():
        db_ids.add(r[0])

    added = json_ids - db_ids
    removed = db_ids - json_ids
    unchanged = json_ids & db_ids

    lines = [
        f"RULES MIGRATION: v{current_version} → v{new_version}",
        f"  Added: {len(added)} — {', '.join(sorted(added)) if added else 'none'}",
        f"  Removed: {len(removed)} — {', '.join(sorted(removed)) if removed else 'none'}",
        f"  Unchanged: {len(unchanged)}",
    ]

    if dry_run:
        lines.append("  Mode: DRY RUN (no changes applied)")
        return "\n".join(lines)

    # Apply additions
    for rid in added:
        r = json_rules[rid]
        conn.execute(
            """INSERT OR REPLACE INTO core_rules (id, category, rule, why, importance, type, added_in, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
            (r["id"], r["category"], r["rule"], r["why"], r["importance"], r["type"], r.get("added_in", new_version))
        )

    # Apply removals (soft delete)
    for rid in removed:
        conn.execute(
            "UPDATE core_rules SET is_active = 0, removed_in = ? WHERE id = ?",
            (new_version, rid)
        )

    # Update existing rules (content might have changed)
    for rid in unchanged:
        r = json_rules[rid]
        conn.execute(
            "UPDATE core_rules SET rule = ?, why = ?, importance = ?, type = ?, category = ? WHERE id = ?",
            (r["rule"], r["why"], r["importance"], r["type"], r["category"], rid)
        )

    conn.execute("UPDATE core_rules_version SET version = ?, updated_at = datetime('now') WHERE id = 1", (new_version,))
    conn.commit()

    lines.append("  Status: APPLIED")
    return "\n".join(lines)


TOOLS = [
    (handle_rules_check, "nexo_rules_check", "Get applicable core rules for an area before acting. Returns BLOCKING and ADVISORY rules."),
    (handle_rules_list, "nexo_rules_list", "List all core rules with status, grouped by category."),
    (handle_rules_migrate, "nexo_rules_migrate", "Sync rules from JSON definition to database. Adds new, soft-deletes removed."),
]
