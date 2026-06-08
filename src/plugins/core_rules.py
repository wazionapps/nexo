"""Core Rules plugin — query and manage versioned behavioral rules."""

import hashlib
import json
import os


def _get_db():
    from db import get_db
    return get_db()


def _rules_file_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "rules",
        "core-rules.json",
    )


def _load_rules_data() -> dict:
    with open(_rules_file_path()) as f:
        return json.load(f)


def _rule_hash(rule: dict, category: str) -> str:
    payload = {
        "category": category,
        "id": rule.get("id", ""),
        "rule": rule.get("rule", ""),
        "why": rule.get("why", ""),
        "importance": rule.get("importance", 0),
        "type": rule.get("type", ""),
        "source_artifact": rule.get("source_artifact", ""),
        "source_anchor": rule.get("source_anchor", ""),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS core_rules (
        id TEXT PRIMARY KEY, category TEXT NOT NULL, rule TEXT NOT NULL,
        why TEXT NOT NULL, importance INTEGER NOT NULL DEFAULT 3,
        type TEXT NOT NULL DEFAULT 'advisory', added_in TEXT DEFAULT '',
        removed_in TEXT DEFAULT NULL, is_active INTEGER NOT NULL DEFAULT 1,
        source_artifact TEXT DEFAULT '', source_anchor TEXT DEFAULT '',
        content_hash TEXT DEFAULT '', protected INTEGER NOT NULL DEFAULT 1,
        severity TEXT DEFAULT 'critical', replacement_rule_id TEXT DEFAULT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS core_rules_version (
        id INTEGER PRIMARY KEY, version TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    for column, ddl in (
        ("source_artifact", "TEXT DEFAULT ''"),
        ("source_anchor", "TEXT DEFAULT ''"),
        ("content_hash", "TEXT DEFAULT ''"),
        ("protected", "INTEGER NOT NULL DEFAULT 1"),
        ("severity", "TEXT DEFAULT 'critical'"),
        ("replacement_rule_id", "TEXT DEFAULT NULL"),
    ):
        try:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(core_rules)").fetchall()}
            if column not in existing:
                conn.execute(f"ALTER TABLE core_rules ADD COLUMN {column} {ddl}")
        except Exception:
            pass
    conn.execute("INSERT OR IGNORE INTO core_rules_version (id, version, updated_at) VALUES (1, '0.0.0', datetime('now'))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_core_rules_category ON core_rules(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_core_rules_active ON core_rules(is_active)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_core_rules_protected ON core_rules(protected, is_active)")


def _flatten_rules(data: dict) -> dict[str, dict]:
    version = data["_meta"]["version"]
    rules = {}
    for cat_key, cat in data["categories"].items():
        for rule in cat["rules"]:
            severity = rule.get("severity") or ("critical" if rule.get("type") == "blocking" and int(rule.get("importance") or 0) >= 5 else "high")
            rules[rule["id"]] = {
                **rule,
                "category": cat_key,
                "added_in": rule.get("added_in", version),
                "content_hash": _rule_hash(rule, cat_key),
                "protected": 0 if rule.get("protected") is False else 1,
                "severity": severity,
                "source_artifact": rule.get("source_artifact", "core-rules.json"),
                "source_anchor": rule.get("source_anchor", rule["id"]),
                "replacement_rule_id": rule.get("replacement_rule_id"),
            }
    return rules


def _sync_rules_from_json(conn=None, dry_run: bool = False) -> dict:
    conn = conn or _get_db()
    _ensure_schema(conn)
    data = _load_rules_data()
    new_version = data["_meta"]["version"]
    json_rules = _flatten_rules(data)

    current_version_row = conn.execute("SELECT version FROM core_rules_version WHERE id = 1").fetchone()
    current_version = current_version_row[0] if current_version_row else "0.0.0"
    db_rows = {
        row["id"]: dict(row)
        for row in conn.execute("SELECT * FROM core_rules WHERE is_active = 1").fetchall()
    }

    added = set(json_rules) - set(db_rows)
    removed = set(db_rows) - set(json_rules)
    changed = {
        rid
        for rid in set(json_rules) & set(db_rows)
        if (db_rows[rid].get("content_hash") or "") != json_rules[rid]["content_hash"]
        or current_version != new_version
    }

    result = {
        "version_from": current_version,
        "version_to": new_version,
        "added": sorted(added),
        "removed": sorted(removed),
        "changed": sorted(changed),
        "active_total": len(json_rules),
        "dry_run": dry_run,
    }
    if dry_run:
        return result

    for rid in sorted(added | changed):
        r = json_rules[rid]
        conn.execute(
            """INSERT OR REPLACE INTO core_rules
               (id, category, rule, why, importance, type, added_in, removed_in, is_active,
                source_artifact, source_anchor, content_hash, protected, severity, replacement_rule_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?, ?, ?, ?, ?)""",
            (
                r["id"],
                r["category"],
                r["rule"],
                r["why"],
                r["importance"],
                r["type"],
                r["added_in"],
                r["source_artifact"],
                r["source_anchor"],
                r["content_hash"],
                r["protected"],
                r["severity"],
                r["replacement_rule_id"],
            ),
        )

    for rid in sorted(removed):
        replacement = db_rows.get(rid, {}).get("replacement_rule_id")
        conn.execute(
            "UPDATE core_rules SET is_active = 0, removed_in = ?, replacement_rule_id = COALESCE(?, replacement_rule_id) WHERE id = ?",
            (new_version, replacement, rid),
        )

    conn.execute("UPDATE core_rules_version SET version = ?, updated_at = datetime('now') WHERE id = 1", (new_version,))
    conn.commit()
    result["status"] = "up_to_date" if not (added or removed or changed or current_version != new_version) else "applied"
    return result


def _sync_if_needed():
    """Keep installed DB rules aligned with packaged product-core JSON."""
    import sys
    if not os.path.exists(_rules_file_path()):
        print(f"[core_rules] WARNING: {_rules_file_path()} not found, skipping sync", file=sys.stderr)
        return
    try:
        _sync_rules_from_json()
    except Exception as e:
        print(f"[core_rules] ERROR syncing rules: {e}", file=sys.stderr)


def handle_rules_check(area: str = "", importance_min: int = 0) -> str:
    """Get applicable core rules for a given area or action.

    Returns BLOCKING rules that must be followed and ADVISORY rules as guidance.
    Call this before taking any significant action.

    Args:
        area: Area of work — 'code', 'delegation', 'communication', 'memory', or empty for all.
              Maps to categories: code→execution+integrity, delegation→delegation, etc.
        importance_min: Minimum importance level (1-5, default 0 = all rules)
    """
    _sync_if_needed()
    conn = _get_db()

    area_to_categories = {
        "code": ("integrity", "execution", "product_core", "bootstrap_contract"),
        "edit": ("integrity", "execution", "product_core", "bootstrap_contract"),
        "delegation": ("delegation",),
        "delegate": ("delegation",),
        "subagent": ("delegation",),
        "communication": ("communication", "product_core"),
        "respond": ("communication", "product_core"),
        "memory": ("memory", "product_core", "bootstrap_contract"),
        "learn": ("memory", "product_core"),
        "proactivity": ("proactivity", "product_core"),
        "protect": ("proactivity", "product_core"),
        "support": ("product_core",),
        "capability": ("product_core",),
        "bootstrap": ("bootstrap_contract",),
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


def handle_rules_list(
    category: str = "",
    filter_category: str = "",
    severity: str = "",
    filter_severity: str = "",
    area: str = "",
    filter_area: str = "",
    limit: int = 0,
) -> str:
    """List all core rules with their status, grouped by category."""
    _sync_if_needed()
    conn = _get_db()

    ver = conn.execute("SELECT version FROM core_rules_version WHERE id = 1").fetchone()
    version = ver[0] if ver else "unknown"

    rows = conn.execute(
        "SELECT id, category, rule, importance, type, is_active, added_in, removed_in FROM core_rules ORDER BY category, id"
    ).fetchall()
    rows = [dict(row) for row in rows]

    wanted_category = (category or filter_category or area or filter_area).strip().lower()
    if wanted_category:
        rows = [row for row in rows if str(row["category"]).strip().lower() == wanted_category]

    wanted_severity = (severity or filter_severity).strip().lower()
    severity_aliases = {
        "block": "blocking",
        "blocking": "blocking",
        "advsr": "advisory",
        "advisory": "advisory",
        "warn": "advisory",
        "warning": "advisory",
    }
    normalized_severity = severity_aliases.get(wanted_severity, "")
    if normalized_severity:
        rows = [row for row in rows if str(row["type"]).strip().lower() == normalized_severity]

    if int(limit or 0) > 0:
        rows = rows[: max(1, int(limit))]

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
    if not os.path.exists(_rules_file_path()):
        return "ERROR: core-rules.json not found"
    result = _sync_rules_from_json(dry_run=dry_run)

    lines = [
        f"RULES MIGRATION: v{result['version_from']} → v{result['version_to']}",
        f"  Added: {len(result['added'])} — {', '.join(result['added']) if result['added'] else 'none'}",
        f"  Removed: {len(result['removed'])} — {', '.join(result['removed']) if result['removed'] else 'none'}",
        f"  Changed: {len(result['changed'])} — {', '.join(result['changed']) if result['changed'] else 'none'}",
        f"  Active total: {result['active_total']}",
    ]

    if dry_run:
        lines.append("  Mode: DRY RUN (no changes applied)")
    else:
        lines.append(f"  Status: {str(result.get('status') or 'APPLIED').upper()}")
    return "\n".join(lines)


TOOLS = [
    (handle_rules_check, "nexo_rules_check", "Get applicable core rules for an area before acting. Returns BLOCKING and ADVISORY rules."),
    (handle_rules_list, "nexo_rules_list", "List all core rules with status, grouped by category."),
    (handle_rules_migrate, "nexo_rules_migrate", "Sync rules from JSON definition to database. Adds new, soft-deletes removed."),
]
