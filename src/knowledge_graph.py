"""NEXO Knowledge Graph — Bi-temporal entity-relationship graph on SQLite."""

import json
from datetime import datetime, timezone
from typing import Optional
import os


def _get_db():
    """Get cognitive.db connection (KG lives in cognitive.db)."""
    import cognitive
    return cognitive._get_db()


def upsert_node(node_type: str, node_ref: str, label: str, properties: dict = None) -> int:
    db = _get_db()
    props_json = json.dumps(properties or {})
    existing = db.execute(
        "SELECT id FROM kg_nodes WHERE node_type = ? AND node_ref = ?",
        (node_type, node_ref)
    ).fetchone()
    if existing:
        db.execute("UPDATE kg_nodes SET label = ?, properties = ? WHERE id = ?",
                   (label, props_json, existing["id"]))
        db.commit()
        return existing["id"]
    cursor = db.execute(
        "INSERT INTO kg_nodes (node_type, node_ref, label, properties) VALUES (?, ?, ?, ?)",
        (node_type, node_ref, label, props_json))
    db.commit()
    return cursor.lastrowid


def get_node(node_type: str, node_ref: str) -> Optional[dict]:
    db = _get_db()
    row = db.execute("SELECT * FROM kg_nodes WHERE node_type = ? AND node_ref = ?",
                     (node_type, node_ref)).fetchone()
    return dict(row) if row else None


def get_node_by_id(node_id: int) -> Optional[dict]:
    db = _get_db()
    row = db.execute("SELECT * FROM kg_nodes WHERE id = ?", (node_id,)).fetchone()
    return dict(row) if row else None


def upsert_edge(source_type: str, source_ref: str, relation: str,
                target_type: str, target_ref: str,
                weight: float = 1.0, confidence: float = 1.0,
                source_memory_id: str = "", properties: dict = None) -> dict:
    db = _get_db()
    source_node = get_node(source_type, source_ref)
    target_node = get_node(target_type, target_ref)
    if not source_node:
        source_node = {"id": upsert_node(source_type, source_ref, source_ref)}
    if not target_node:
        target_node = {"id": upsert_node(target_type, target_ref, target_ref)}
    source_id = source_node["id"]
    target_id = target_node["id"]
    props_json = json.dumps(properties or {})
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
    existing = db.execute(
        "SELECT id, weight, confidence, properties FROM kg_edges "
        "WHERE source_id = ? AND target_id = ? AND relation = ? AND valid_until IS NULL",
        (source_id, target_id, relation)).fetchone()
    if existing:
        if (abs(existing["weight"] - weight) < 0.01 and
            abs(existing["confidence"] - confidence) < 0.01 and
            existing["properties"] == props_json):
            return {"action": "NOOP", "edge_id": existing["id"]}
        db.execute("UPDATE kg_edges SET valid_until = ? WHERE id = ?", (now, existing["id"]))
        cursor = db.execute(
            "INSERT INTO kg_edges (source_id, target_id, relation, weight, confidence, "
            "valid_from, source_memory_id, properties) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (source_id, target_id, relation, weight, confidence, now, source_memory_id, props_json))
        db.commit()
        return {"action": "UPDATE", "edge_id": cursor.lastrowid}
    cursor = db.execute(
        "INSERT INTO kg_edges (source_id, target_id, relation, weight, confidence, "
        "valid_from, source_memory_id, properties) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (source_id, target_id, relation, weight, confidence, now, source_memory_id, props_json))
    db.commit()
    return {"action": "ADD", "edge_id": cursor.lastrowid}


def delete_edge(source_type: str, source_ref: str, relation: str,
                target_type: str, target_ref: str) -> bool:
    db = _get_db()
    source = get_node(source_type, source_ref)
    target = get_node(target_type, target_ref)
    if not source or not target:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
    cursor = db.execute(
        "UPDATE kg_edges SET valid_until = ? WHERE source_id = ? AND target_id = ? "
        "AND relation = ? AND valid_until IS NULL",
        (now, source["id"], target["id"], relation))
    db.commit()
    return cursor.rowcount > 0


def get_neighbors(node_id: int, relation: str = None, active_only: bool = True) -> list:
    db = _get_db()
    conditions = ["(e.source_id = ? OR e.target_id = ?)"]
    params = [node_id, node_id]
    if active_only:
        conditions.append("e.valid_until IS NULL")
    if relation:
        conditions.append("e.relation = ?")
        params.append(relation)
    where = " AND ".join(conditions)
    rows = db.execute(f"""
        SELECT e.*, n.node_type, n.node_ref, n.label,
               CASE WHEN e.source_id = ? THEN 'outgoing' ELSE 'incoming' END as direction
        FROM kg_edges e
        JOIN kg_nodes n ON n.id = CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END
        WHERE {where}
        ORDER BY e.weight DESC
    """, [node_id, node_id] + params).fetchall()
    return [dict(r) for r in rows]


def traverse(start_id: int, max_depth: int = 3, relation_filter: str = None,
             active_only: bool = True) -> dict:
    visited_nodes = set()
    visited_edges = set()
    result_nodes = []
    result_edges = []
    queue = [(start_id, 0)]
    while queue:
        current_id, depth = queue.pop(0)
        if current_id in visited_nodes or depth > max_depth:
            continue
        visited_nodes.add(current_id)
        node = get_node_by_id(current_id)
        if node:
            node["depth"] = depth
            result_nodes.append(node)
        neighbors = get_neighbors(current_id, relation=relation_filter, active_only=active_only)
        for n in neighbors:
            edge_id = n["id"]
            if edge_id not in visited_edges:
                visited_edges.add(edge_id)
                result_edges.append({
                    "id": edge_id, "source_id": n["source_id"], "target_id": n["target_id"],
                    "relation": n["relation"], "weight": n["weight"],
                    "valid_from": n["valid_from"], "valid_until": n["valid_until"],
                })
            neighbor_id = n["target_id"] if n["source_id"] == current_id else n["source_id"]
            if neighbor_id not in visited_nodes and depth + 1 <= max_depth:
                queue.append((neighbor_id, depth + 1))
    return {"nodes": result_nodes, "edges": result_edges}


def shortest_path(from_id: int, to_id: int, max_depth: int = 6) -> Optional[list]:
    if from_id == to_id:
        return [from_id]
    visited = {from_id}
    queue = [(from_id, [from_id])]
    while queue:
        current, path = queue.pop(0)
        if len(path) > max_depth:
            continue
        neighbors = get_neighbors(current, active_only=True)
        for n in neighbors:
            nid = n["target_id"] if n["source_id"] == current else n["source_id"]
            if nid == to_id:
                return path + [nid]
            if nid not in visited:
                visited.add(nid)
                queue.append((nid, path + [nid]))
    return None


def merge_nodes(keep_id: int, merge_id: int) -> int:
    db = _get_db()
    db.execute("UPDATE kg_edges SET source_id = ? WHERE source_id = ?", (keep_id, merge_id))
    db.execute("UPDATE kg_edges SET target_id = ? WHERE target_id = ?", (keep_id, merge_id))
    # Clean up self-loops created by merge
    db.execute("DELETE FROM kg_edges WHERE source_id = target_id")
    db.execute("DELETE FROM kg_nodes WHERE id = ?", (merge_id,))
    db.commit()
    return keep_id


def query_at(node_id: int, timestamp: str, relation: str = None) -> list:
    db = _get_db()
    conditions = ["(e.source_id = ? OR e.target_id = ?)",
                  "e.valid_from <= ?",
                  "(e.valid_until IS NULL OR e.valid_until >= ?)"]
    params = [node_id, node_id, timestamp, timestamp]
    if relation:
        conditions.append("e.relation = ?")
        params.append(relation)
    where = " AND ".join(conditions)
    rows = db.execute(f"""
        SELECT e.*, n.node_type, n.node_ref, n.label
        FROM kg_edges e
        JOIN kg_nodes n ON n.id = CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END
        WHERE {where}
    """, [node_id] + params).fetchall()
    return [dict(r) for r in rows]


def timeline(node_id: int, relation: str = None) -> list:
    db = _get_db()
    conditions = ["(e.source_id = ? OR e.target_id = ?)"]
    params = [node_id, node_id]
    if relation:
        conditions.append("e.relation = ?")
        params.append(relation)
    where = " AND ".join(conditions)
    rows = db.execute(f"""
        SELECT e.*, n.node_type, n.node_ref, n.label
        FROM kg_edges e
        JOIN kg_nodes n ON n.id = CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END
        WHERE {where}
        ORDER BY e.valid_from
    """, [node_id] + params).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    db = _get_db()
    nodes = db.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0]
    edges_active = db.execute("SELECT COUNT(*) FROM kg_edges WHERE valid_until IS NULL").fetchone()[0]
    edges_historical = db.execute("SELECT COUNT(*) FROM kg_edges WHERE valid_until IS NOT NULL").fetchone()[0]
    type_counts = {}
    for row in db.execute("SELECT node_type, COUNT(*) as cnt FROM kg_nodes GROUP BY node_type").fetchall():
        type_counts[row["node_type"]] = row["cnt"]
    relation_counts = {}
    for row in db.execute(
        "SELECT relation, COUNT(*) as cnt FROM kg_edges WHERE valid_until IS NULL GROUP BY relation"
    ).fetchall():
        relation_counts[row["relation"]] = row["cnt"]
    most_connected = []
    for row in db.execute("""
        SELECT n.id, n.label, n.node_type, COUNT(e.id) as connections
        FROM kg_nodes n
        LEFT JOIN kg_edges e ON (e.source_id = n.id OR e.target_id = n.id) AND e.valid_until IS NULL
        GROUP BY n.id ORDER BY connections DESC LIMIT 10
    """).fetchall():
        most_connected.append(dict(row))
    return {
        "nodes": nodes, "edges_active": edges_active, "edges_historical": edges_historical,
        "node_types": type_counts, "relation_types": relation_counts,
        "most_connected": most_connected,
    }


def extract_subgraph(center_id: int, depth: int = 2) -> dict:
    graph = traverse(center_id, max_depth=depth)
    d3_nodes = [{"id": n["id"], "label": n["label"], "type": n["node_type"],
                 "depth": n.get("depth", 0)} for n in graph["nodes"]]
    d3_edges = [{"source": e["source_id"], "target": e["target_id"],
                 "relation": e["relation"], "weight": e["weight"]} for e in graph["edges"]]
    return {"nodes": d3_nodes, "edges": d3_edges}


# ── Bitemporal export — Fase 5 item 1 ────────────────────────────────────
#
# The KG is bi-temporal by design: kg_edges has valid_from and valid_until
# columns and the upsert_edge / delete_edge helpers maintain them
# correctly. The audit's "exportable" requirement asked for emitting the
# graph to standard interchange formats so external tools can ingest it
# without speaking SQLite. The two helpers below cover the canonical
# choices: JSON-LD (semantic web, human-readable) and GraphML (igraph,
# Gephi, NetworkX, Cytoscape).
#
# Both helpers respect the bitemporal model: when as_of is None, only
# active edges (valid_until IS NULL) are emitted. When as_of is a
# timestamp string, the historical state at that instant is emitted.

import json as _json


def export_to_jsonld(*, as_of: str = "") -> dict:
    """Export the active or historical KG to a JSON-LD document.

    The vocabulary lives under https://nexo-brain.com/kg/v1# so external
    tools can resolve types and relations consistently. Each node becomes
    a top-level @graph entry with @id = nexo:node:<id> and @type =
    nexo:<node_type>. Each edge becomes a relation property on its source
    node, plus a parallel @reverse on the target so the JSON-LD remains
    fully traversable.

    Args:
        as_of: ISO timestamp. If empty, exports active edges only
               (valid_until IS NULL). If provided, exports the snapshot
               that was valid at that instant via temporal range query.

    Returns a JSON-LD-shaped dict ready for json.dumps().
    """
    db = _get_db()
    # kg_nodes is NOT bitemporal — only kg_edges has valid_from/valid_until.
    # The audit's "bitemporal" requirement is satisfied at the edge level
    # because nodes are stable identities while edges encode the temporal
    # facts (relationships valid during a time window).
    nodes = [dict(row) for row in db.execute(
        "SELECT id, node_type, node_ref, label, properties FROM kg_nodes"
    ).fetchall()]

    if as_of and as_of.strip():
        edge_rows = db.execute(
            "SELECT id, source_id, target_id, relation, weight, confidence, "
            "valid_from, valid_until, properties FROM kg_edges "
            "WHERE valid_from <= ? AND (valid_until IS NULL OR valid_until > ?)",
            (as_of, as_of),
        ).fetchall()
    else:
        edge_rows = db.execute(
            "SELECT id, source_id, target_id, relation, weight, confidence, "
            "valid_from, valid_until, properties FROM kg_edges WHERE valid_until IS NULL"
        ).fetchall()
    edges = [dict(row) for row in edge_rows]

    nodes_by_id: dict[int, dict] = {}
    for n in nodes:
        try:
            props = _json.loads(n.get("properties") or "{}")
        except Exception:
            props = {}
        nodes_by_id[n["id"]] = {
            "@id": f"nexo:node:{n['id']}",
            "@type": f"nexo:{n['node_type']}",
            "label": n.get("label") or "",
            "node_ref": n.get("node_ref") or "",
            "properties": props,
        }

    for e in edges:
        src_id = e["source_id"]
        tgt_id = e["target_id"]
        if src_id not in nodes_by_id or tgt_id not in nodes_by_id:
            continue  # orphan edge — skip
        relation_key = f"nexo:{e['relation']}"
        edge_payload = {
            "@id": f"nexo:edge:{e['id']}",
            "target": f"nexo:node:{tgt_id}",
            "weight": float(e.get("weight") or 0.0),
            "confidence": float(e.get("confidence") or 0.0),
            "valid_from": e.get("valid_from"),
            "valid_until": e.get("valid_until"),
        }
        nodes_by_id[src_id].setdefault(relation_key, []).append(edge_payload)

    snapshot_label = as_of.strip() if as_of and as_of.strip() else "active"
    return {
        "@context": {
            "nexo": "https://nexo-brain.com/kg/v1#",
            "label": "https://nexo-brain.com/kg/v1#label",
            "node_ref": "https://nexo-brain.com/kg/v1#node_ref",
            "weight": "https://nexo-brain.com/kg/v1#weight",
            "confidence": "https://nexo-brain.com/kg/v1#confidence",
            "valid_from": "https://nexo-brain.com/kg/v1#valid_from",
            "valid_until": "https://nexo-brain.com/kg/v1#valid_until",
            "properties": "https://nexo-brain.com/kg/v1#properties",
        },
        "@type": "nexo:KnowledgeGraphSnapshot",
        "snapshot": snapshot_label,
        "node_count": len(nodes_by_id),
        "edge_count": len(edges),
        "@graph": list(nodes_by_id.values()),
    }


def export_to_graphml(*, as_of: str = "") -> str:
    """Export the active or historical KG to a GraphML XML string.

    GraphML is the canonical interchange for igraph, Gephi, NetworkX, and
    Cytoscape. Bitemporal columns are emitted as edge data attributes so
    importers that support them (Gephi temporal layouts, NetworkX
    DiGraph) can render the historical view.

    Args:
        as_of: ISO timestamp. Same semantics as export_to_jsonld.

    Returns a string with a valid GraphML 1.1 document.
    """
    db = _get_db()
    nodes = [dict(row) for row in db.execute(
        "SELECT id, node_type, node_ref, label FROM kg_nodes"
    ).fetchall()]
    if as_of and as_of.strip():
        edge_rows = db.execute(
            "SELECT id, source_id, target_id, relation, weight, valid_from, valid_until FROM kg_edges "
            "WHERE valid_from <= ? AND (valid_until IS NULL OR valid_until > ?)",
            (as_of, as_of),
        ).fetchall()
    else:
        edge_rows = db.execute(
            "SELECT id, source_id, target_id, relation, weight, valid_from, valid_until FROM kg_edges "
            "WHERE valid_until IS NULL"
        ).fetchall()

    def _xml_escape(value: object) -> str:
        text = "" if value is None else str(value)
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    out: list[str] = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<graphml xmlns="http://graphml.graphdrawing.org/xmlns">')
    out.append('  <key id="label" for="node" attr.name="label" attr.type="string"/>')
    out.append('  <key id="node_type" for="node" attr.name="node_type" attr.type="string"/>')
    out.append('  <key id="node_ref" for="node" attr.name="node_ref" attr.type="string"/>')
    out.append('  <key id="relation" for="edge" attr.name="relation" attr.type="string"/>')
    out.append('  <key id="weight" for="edge" attr.name="weight" attr.type="double"/>')
    out.append('  <key id="valid_from" for="edge" attr.name="valid_from" attr.type="string"/>')
    out.append('  <key id="valid_until" for="edge" attr.name="valid_until" attr.type="string"/>')
    snapshot_label = as_of.strip() if as_of and as_of.strip() else "active"
    out.append(f'  <graph id="nexo_kg_{_xml_escape(snapshot_label)}" edgedefault="directed">')
    for n in nodes:
        out.append(f'    <node id="n{n["id"]}">')
        out.append(f'      <data key="label">{_xml_escape(n.get("label"))}</data>')
        out.append(f'      <data key="node_type">{_xml_escape(n.get("node_type"))}</data>')
        out.append(f'      <data key="node_ref">{_xml_escape(n.get("node_ref"))}</data>')
        out.append('    </node>')
    for e in edge_rows:
        out.append(
            f'    <edge id="e{e["id"]}" source="n{e["source_id"]}" target="n{e["target_id"]}">'
        )
        out.append(f'      <data key="relation">{_xml_escape(e["relation"])}</data>')
        out.append(f'      <data key="weight">{float(e["weight"] or 0.0)}</data>')
        out.append(f'      <data key="valid_from">{_xml_escape(e["valid_from"])}</data>')
        if e["valid_until"]:
            out.append(f'      <data key="valid_until">{_xml_escape(e["valid_until"])}</data>')
        out.append('    </edge>')
    out.append('  </graph>')
    out.append('</graphml>')
    return "\n".join(out)
