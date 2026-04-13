"""Knowledge Graph MCP tools — query, path, neighbors, stats."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import knowledge_graph as kg


def _find_node(node_type: str, node_ref: str):
    """Find a node, trying both raw ref and type-prefixed ref (area:X, file:X)."""
    node = kg.get_node(node_type, node_ref)
    if not node:
        node = kg.get_node(node_type, f"{node_type}:{node_ref}")
    return node


def handle_kg_query(node_type: str, node_ref: str, depth: int = 2, relation: str = "") -> str:
    """Traverse the knowledge graph from a node up to `depth` hops."""
    node = _find_node(node_type, node_ref)
    if not node:
        return f"Node not found: {node_type}/{node_ref}"
    result = kg.traverse(node["id"], max_depth=depth, relation_filter=relation or None)
    nodes = result["nodes"][:30]
    edges = result["edges"][:30]
    lines = [f"KG TRAVERSE — {node['label']} ({node_type}/{node_ref}) depth={depth}"]
    lines.append(f"Nodes: {len(result['nodes'])}  Edges: {len(result['edges'])}")
    lines.append("")
    lines.append("NODES:")
    for n in nodes:
        indent = "  " * n.get("depth", 0)
        lines.append(f"  {indent}[{n['id']}] ({n['node_type']}) {n['label']} — {n['node_ref']}")
    lines.append("")
    lines.append("EDGES:")
    for e in edges[:20]:
        lines.append(f"  [{e['source_id']}] --{e['relation']}--> [{e['target_id']}]  w={e['weight']}")
    return "\n".join(lines)


def handle_kg_path(from_type: str, from_ref: str, to_type: str, to_ref: str) -> str:
    """Find the shortest path between two nodes in the knowledge graph."""
    from_node = _find_node(from_type, from_ref)
    if not from_node:
        return f"Source node not found: {from_type}/{from_ref}"
    to_node = _find_node(to_type, to_ref)
    if not to_node:
        return f"Target node not found: {to_type}/{to_ref}"
    path_ids = kg.shortest_path(from_node["id"], to_node["id"])
    if not path_ids:
        return f"No path found between {from_ref} and {to_ref}"
    lines = [f"PATH ({len(path_ids) - 1} hops): {from_ref} → {to_ref}"]
    for i, nid in enumerate(path_ids):
        node = kg.get_node_by_id(nid)
        label = node["label"] if node else f"[{nid}]"
        ntype = node["node_type"] if node else "?"
        lines.append(f"  {i}. [{nid}] ({ntype}) {label}")
    return "\n".join(lines)


def handle_kg_neighbors(node_type: str, node_ref: str, relation: str = "") -> str:
    """Get direct neighbors of a node, optionally filtered by relation type."""
    node = _find_node(node_type, node_ref)
    if not node:
        return f"Node not found: {node_type}/{node_ref}"
    neighbors = kg.get_neighbors(node["id"], relation=relation or None)
    if not neighbors:
        rel_info = f" (relation={relation})" if relation else ""
        return f"No neighbors found for {node['label']}{rel_info}"
    lines = [f"NEIGHBORS of [{node['id']}] {node['label']} ({len(neighbors)} total):"]
    for n in neighbors[:30]:
        direction = n.get("direction", "?")
        arrow = "-->" if direction == "outgoing" else "<--"
        lines.append(f"  {arrow} [{n['id']}] {n['label']} ({n['node_type']})  rel={n['relation']}  w={n['weight']}")
    if len(neighbors) > 30:
        lines.append(f"  ... +{len(neighbors) - 30} more")
    return "\n".join(lines)


def handle_kg_stats() -> str:
    """Return knowledge graph statistics: node counts, edge counts, top connected nodes."""
    s = kg.stats()
    lines = ["KNOWLEDGE GRAPH STATS"]
    lines.append(f"  Nodes: {s['nodes']}")
    lines.append(f"  Edges (active): {s['edges_active']}")
    lines.append(f"  Edges (historical): {s['edges_historical']}")
    if s["node_types"]:
        lines.append("\nNODE TYPES:")
        for t, cnt in sorted(s["node_types"].items(), key=lambda x: -x[1]):
            lines.append(f"  {t}: {cnt}")
    if s["relation_types"]:
        lines.append("\nRELATION TYPES:")
        for r, cnt in sorted(s["relation_types"].items(), key=lambda x: -x[1])[:20]:
            lines.append(f"  {r}: {cnt}")
    if s["most_connected"]:
        lines.append("\nMOST CONNECTED:")
        for n in s["most_connected"][:10]:
            lines.append(f"  [{n['id']}] {n['label']} ({n['node_type']}) — {n['connections']} connections")
    return "\n".join(lines)


def handle_kg_export(format: str = "jsonld", as_of: str = "") -> str:
    """Export the bitemporal knowledge graph to a standard interchange format.

    Closes Fase 5 item 1 of NEXO-AUDIT-2026-04-11. The KG was already
    bitemporal (kg_edges has valid_from and valid_until and the
    upsert/delete helpers maintain them), but had no way to emit the
    graph in a format external tools can ingest. This tool wraps the
    two canonical exporters in cognitive.knowledge_graph.

    Args:
        format: 'jsonld' (default, semantic web / human-readable) or
                'graphml' (igraph, Gephi, NetworkX, Cytoscape).
        as_of: Optional ISO timestamp. If empty, exports the active
                snapshot. If provided, exports the historical snapshot
                that was valid at that instant.
    """
    import json as _json
    import knowledge_graph as kg

    fmt = (format or "jsonld").strip().lower()
    if fmt == "jsonld":
        payload = kg.export_to_jsonld(as_of=as_of)
        return _json.dumps(payload, ensure_ascii=False, indent=2)
    if fmt == "graphml":
        return kg.export_to_graphml(as_of=as_of)
    return _json.dumps(
        {"ok": False, "error": f"unsupported format: {format!r} (use jsonld or graphml)"},
        ensure_ascii=False,
    )


TOOLS = [
    (handle_kg_query, "nexo_kg_query", "Query knowledge graph — traverse from a node"),
    (handle_kg_path, "nexo_kg_path", "Find shortest path between two nodes"),
    (handle_kg_neighbors, "nexo_kg_neighbors", "Get direct neighbors of a node"),
    (handle_kg_stats, "nexo_kg_stats", "Knowledge graph statistics"),
    (handle_kg_export, "nexo_kg_export", "Export the bitemporal KG to JSON-LD or GraphML (active snapshot or historical via as_of)"),
]
