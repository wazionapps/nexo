from __future__ import annotations

import json
from pathlib import Path


def _catalog():
    path = Path(__file__).resolve().parents[1] / "src" / "rules" / "core-rules.json"
    return json.loads(path.read_text())


def test_core_rules_catalog_metadata_matches_active_rules():
    data = _catalog()
    rules = [
        rule
        for category in data["categories"].values()
        for rule in category["rules"]
    ]
    blocking = [rule for rule in rules if rule["type"] == "blocking"]

    assert data["_meta"]["version"] == "1.1.0"
    assert data["_meta"]["total_rules"] == len(rules)
    assert data["_meta"]["blocking"] == len(blocking)
    assert {rule["id"] for rule in rules} >= {
        "CORE_USER_SEPARATION",
        "MEMORY_AUTHORITY",
        "PC1",
        "PC13",
        "PC20",
        "PC28",
        "PC32",
    }


def test_core_rules_sync_installs_product_core_rows_with_hashes(isolated_db):
    import db
    from plugins.core_rules import handle_rules_list

    rendered = handle_rules_list(limit=1)
    assert "CORE RULES v1.1.0" in rendered

    conn = db.get_db()
    total = conn.execute("SELECT COUNT(*) FROM core_rules WHERE is_active = 1").fetchone()[0]
    version = conn.execute("SELECT version FROM core_rules_version WHERE id = 1").fetchone()[0]
    pc1 = conn.execute("SELECT * FROM core_rules WHERE id = 'PC1'").fetchone()
    bootstrap = conn.execute("SELECT * FROM core_rules WHERE id = 'CORE_USER_SEPARATION'").fetchone()

    assert version == "1.1.0"
    assert total == _catalog()["_meta"]["total_rules"]
    assert pc1["category"] == "product_core"
    assert pc1["protected"] == 1
    assert pc1["content_hash"]
    assert bootstrap["source_artifact"]
    assert bootstrap["source_anchor"]


def test_core_rules_sync_updates_non_empty_old_registry(isolated_db):
    import db
    from plugins.core_rules import _sync_rules_from_json

    conn = db.get_db()
    conn.execute(
        """INSERT OR REPLACE INTO core_rules
           (id, category, rule, why, importance, type, added_in, is_active)
           VALUES ('I1', 'integrity', 'old text', 'old why', 5, 'blocking', '1.0.0', 1)"""
    )
    conn.execute("UPDATE core_rules_version SET version = '1.0.0' WHERE id = 1")
    conn.commit()

    result = _sync_rules_from_json()

    assert result["version_from"] == "1.0.0"
    assert "PC1" in result["added"]
    assert "I1" in result["changed"]
    row = conn.execute("SELECT rule, content_hash FROM core_rules WHERE id = 'I1'").fetchone()
    assert row["rule"] != "old text"
    assert row["content_hash"]
