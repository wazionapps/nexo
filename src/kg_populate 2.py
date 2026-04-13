"""NEXO KG Auto-Population — backfill from nexo.db + incremental hooks."""

import json
import os
import sqlite3
from typing import Optional

import knowledge_graph as kg
from db import get_db


# ─── helpers ────────────────────────────────────────────────────────────────

def _cognitive_db():
    """Direct cognitive.db connection (for somatic_markers)."""
    nexo_home = os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))
    data_dir = os.path.join(nexo_home, "data")
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "cognitive.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_files(files_str: str) -> list[str]:
    """Extract individual file paths from a comma/newline-separated string."""
    if not files_str:
        return []
    parts = [p.strip() for p in files_str.replace("\n", ",").split(",")]
    return [p for p in parts if p]


# ─── backfill functions ──────────────────────────────────────────────────────

def backfill_entities() -> int:
    """Read entities table → create entity nodes in KG."""
    db = get_db()
    rows = db.execute("SELECT id, name, type, value, notes FROM entities").fetchall()
    count = 0
    for row in rows:
        props = {}
        if row["value"]:
            props["value"] = row["value"]
        if row["notes"]:
            props["notes"] = row["notes"]
        kg.upsert_node(
            node_type="entity",
            node_ref=f"entity:{row['id']}",
            label=row["name"],
            properties={"entity_type": row["type"], **props},
        )
        count += 1
    return count


def backfill_learnings() -> int:
    """Read learnings → create learning nodes + file/area edges."""
    db = get_db()
    rows = db.execute(
        "SELECT id, category, title, applies_to FROM learnings WHERE status != 'deleted'"
    ).fetchall()
    count = 0
    for row in rows:
        learning_ref = f"learning:{row['id']}"
        kg.upsert_node(
            node_type="learning",
            node_ref=learning_ref,
            label=row["title"] or f"Learning #{row['id']}",
            properties={"category": row["category"]},
        )
        # edge: learning → category/area
        if row["category"]:
            kg.upsert_edge(
                source_type="learning", source_ref=learning_ref,
                relation="belongs_to",
                target_type="area", target_ref=f"area:{row['category']}",
                weight=1.0,
            )
        # edge: learning → file (from applies_to)
        applies = row["applies_to"] or ""
        for fpath in _parse_files(applies):
            if fpath:
                kg.upsert_edge(
                    source_type="learning", source_ref=learning_ref,
                    relation="applies_to_file",
                    target_type="file", target_ref=f"file:{fpath}",
                    weight=0.8,
                )
        count += 1
    return count


def backfill_changes() -> int:
    """Read change_log → create file nodes + file→area edges."""
    db = get_db()
    rows = db.execute("SELECT id, files, what_changed FROM change_log").fetchall()
    count = 0
    for row in rows:
        change_ref = f"change:{row['id']}"
        kg.upsert_node(
            node_type="change",
            node_ref=change_ref,
            label=f"Change #{row['id']}",
            properties={"summary": (row["what_changed"] or "")[:120]},
        )
        for fpath in _parse_files(row["files"] or ""):
            file_ref = f"file:{fpath}"
            kg.upsert_node(
                node_type="file",
                node_ref=file_ref,
                label=os.path.basename(fpath) or fpath,
            )
            kg.upsert_edge(
                source_type="change", source_ref=change_ref,
                relation="touched",
                target_type="file", target_ref=file_ref,
                weight=1.0,
            )
        count += 1
    return count


def backfill_decisions() -> int:
    """Read decisions → create decision nodes + decision→area edges."""
    db = get_db()
    rows = db.execute("SELECT id, domain, decision, status FROM decisions").fetchall()
    count = 0
    for row in rows:
        decision_ref = f"decision:{row['id']}"
        kg.upsert_node(
            node_type="decision",
            node_ref=decision_ref,
            label=(row["decision"] or "")[:80] or f"Decision #{row['id']}",
            properties={"domain": row["domain"], "status": row["status"]},
        )
        if row["domain"]:
            kg.upsert_edge(
                source_type="decision", source_ref=decision_ref,
                relation="in_domain",
                target_type="area", target_ref=f"area:{row['domain']}",
                weight=1.0,
            )
        count += 1
    return count


def backfill_somatic() -> int:
    """Read somatic_markers from cognitive.db → create file/area nodes with risk."""
    cdb = _cognitive_db()
    try:
        rows = cdb.execute(
            "SELECT target, target_type, risk_score, incident_count FROM somatic_markers"
        ).fetchall()
        count = 0
        for row in rows:
            target_type = row["target_type"] or "file"
            node_ref = f"{target_type}:{row['target']}"
            kg.upsert_node(
                node_type=target_type,
                node_ref=node_ref,
                label=os.path.basename(row["target"]) or row["target"],
                properties={
                    "risk_score": row["risk_score"],
                    "incident_count": row["incident_count"],
                },
            )
            count += 1
        return count
    finally:
        cdb.close()


def run_full_backfill() -> dict:
    """Run all backfill functions. Idempotent (upsert-based)."""
    results = {}
    results["entities"] = backfill_entities()
    results["learnings"] = backfill_learnings()
    results["changes"] = backfill_changes()
    results["decisions"] = backfill_decisions()
    results["somatic"] = backfill_somatic()
    results["total"] = sum(results.values())
    return results


# ─── incremental hooks ───────────────────────────────────────────────────────

def on_learning_add(learning_id: int, category: str, title: str, applies_to: str = "") -> None:
    try:
        learning_ref = f"learning:{learning_id}"
        kg.upsert_node(
            node_type="learning",
            node_ref=learning_ref,
            label=title or f"Learning #{learning_id}",
            properties={"category": category},
        )
        if category:
            kg.upsert_edge(
                source_type="learning", source_ref=learning_ref,
                relation="belongs_to",
                target_type="area", target_ref=f"area:{category}",
                weight=1.0,
            )
        for fpath in _parse_files(applies_to or ""):
            if fpath:
                kg.upsert_edge(
                    source_type="learning", source_ref=learning_ref,
                    relation="applies_to_file",
                    target_type="file", target_ref=f"file:{fpath}",
                    weight=0.8,
                )
    except Exception:
        pass


def on_change_log(change_id: int, files: str, system: str = "") -> None:
    try:
        change_ref = f"change:{change_id}"
        kg.upsert_node(
            node_type="change",
            node_ref=change_ref,
            label=f"Change #{change_id}",
        )
        for fpath in _parse_files(files or ""):
            file_ref = f"file:{fpath}"
            kg.upsert_node(
                node_type="file",
                node_ref=file_ref,
                label=os.path.basename(fpath) or fpath,
            )
            kg.upsert_edge(
                source_type="change", source_ref=change_ref,
                relation="touched",
                target_type="file", target_ref=file_ref,
                weight=1.0,
            )
        if system:
            kg.upsert_edge(
                source_type="change", source_ref=change_ref,
                relation="in_system",
                target_type="area", target_ref=f"area:{system}",
                weight=1.0,
            )
    except Exception:
        pass


def on_decision_log(decision_id: int, domain: str, decision_text: str) -> None:
    try:
        decision_ref = f"decision:{decision_id}"
        kg.upsert_node(
            node_type="decision",
            node_ref=decision_ref,
            label=(decision_text or "")[:80] or f"Decision #{decision_id}",
            properties={"domain": domain},
        )
        if domain:
            kg.upsert_edge(
                source_type="decision", source_ref=decision_ref,
                relation="in_domain",
                target_type="area", target_ref=f"area:{domain}",
                weight=1.0,
            )
    except Exception:
        pass


def on_entity_create(entity_id: int, name: str, entity_type: str) -> None:
    try:
        kg.upsert_node(
            node_type="entity",
            node_ref=f"entity:{entity_id}",
            label=name,
            properties={"entity_type": entity_type},
        )
    except Exception:
        pass


# ─── main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running full KG backfill...")
    results = run_full_backfill()
    print("\nBackfill complete:")
    for key, val in results.items():
        if key != "total":
            print(f"  {key:12s}: {val:4d} records")
    print(f"  {'TOTAL':12s}: {results['total']:4d} nodes/edges processed")

    # Show KG stats
    s = kg.stats()
    print(f"\nKG state: {s['nodes']} nodes, {s['edges_active']} active edges")
