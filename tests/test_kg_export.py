"""Tests for the bitemporal KG export — Fase 5 item 1.

Pin the JSON-LD and GraphML exporters and verify that the bitemporal
contract (active vs as_of historical snapshots) is honored. The KG
itself was already bitemporal before this audit phase; this is the
exporter side of the gap.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _seed_kg(*, multi_edge: bool = False) -> dict:
    """Insert a tiny graph and return the {node_a_id, node_b_id, edge_id} map."""
    import knowledge_graph as kg
    a = kg.upsert_node("learning", "L1", "Learning One", {"category": "test"})
    b = kg.upsert_node("decision", "D1", "Decision One", {"impact": "low"})
    edge_id = kg.upsert_edge(
        "learning", "L1", "informs",
        "decision", "D1",
        weight=0.85, confidence=0.9,
        properties={"reason": "anchor test"},
    )
    result = {"a": a, "b": b, "edge": edge_id}
    if multi_edge:
        c = kg.upsert_node("entity", "E1", "Entity One")
        result["c"] = c
        result["edge2"] = kg.upsert_edge(
            "decision", "D1", "affects",
            "entity", "E1",
            weight=0.6,
        )
    return result


# ── JSON-LD export ────────────────────────────────────────────────────────


class TestExportToJsonld:
    def test_returns_dict_with_jsonld_envelope(self, isolated_db):
        from knowledge_graph import export_to_jsonld
        payload = export_to_jsonld()
        assert isinstance(payload, dict)
        assert "@context" in payload
        assert payload["@type"] == "nexo:KnowledgeGraphSnapshot"
        assert "@graph" in payload
        assert payload["snapshot"] == "active"

    def test_empty_kg_returns_zero_counts(self, isolated_db):
        from knowledge_graph import export_to_jsonld
        payload = export_to_jsonld()
        assert payload["node_count"] == 0
        assert payload["edge_count"] == 0
        assert payload["@graph"] == []

    def test_seeded_kg_emits_nodes_and_relations(self, isolated_db):
        ids = _seed_kg()
        from knowledge_graph import export_to_jsonld
        payload = export_to_jsonld()

        assert payload["node_count"] == 2
        assert payload["edge_count"] == 1

        nodes_by_id = {n["@id"]: n for n in payload["@graph"]}
        assert f"nexo:node:{ids['a']}" in nodes_by_id
        assert f"nexo:node:{ids['b']}" in nodes_by_id

        source_node = nodes_by_id[f"nexo:node:{ids['a']}"]
        assert source_node["@type"] == "nexo:learning"
        assert source_node["label"] == "Learning One"
        assert "nexo:informs" in source_node
        relations = source_node["nexo:informs"]
        assert len(relations) == 1
        assert relations[0]["target"] == f"nexo:node:{ids['b']}"
        assert relations[0]["weight"] == pytest.approx(0.85)
        assert relations[0]["confidence"] == pytest.approx(0.9)

    def test_inactive_edge_excluded_from_active_snapshot(self, isolated_db):
        ids = _seed_kg()
        from knowledge_graph import export_to_jsonld, delete_edge

        # Tombstone the edge — its valid_until becomes set.
        delete_edge("learning", "L1", "informs", "decision", "D1")

        payload = export_to_jsonld()
        # Active snapshot must NOT include the now-historical edge.
        assert payload["edge_count"] == 0


# ── GraphML export ────────────────────────────────────────────────────────


class TestExportToGraphml:
    def test_returns_well_formed_xml(self, isolated_db):
        from knowledge_graph import export_to_graphml
        xml = export_to_graphml()
        assert xml.startswith("<?xml")
        assert "<graphml" in xml
        # Must parse cleanly with stdlib XML.
        root = ET.fromstring(xml)
        assert root.tag.endswith("graphml")

    def test_empty_kg_emits_empty_graph(self, isolated_db):
        from knowledge_graph import export_to_graphml
        xml = export_to_graphml()
        root = ET.fromstring(xml)
        ns = "{http://graphml.graphdrawing.org/xmlns}"
        graph = root.find(f"{ns}graph")
        assert graph is not None
        assert graph.findall(f"{ns}node") == []
        assert graph.findall(f"{ns}edge") == []

    def test_seeded_kg_emits_node_and_edge(self, isolated_db):
        ids = _seed_kg()
        from knowledge_graph import export_to_graphml
        xml = export_to_graphml()
        root = ET.fromstring(xml)
        ns = "{http://graphml.graphdrawing.org/xmlns}"
        graph = root.find(f"{ns}graph")
        nodes = graph.findall(f"{ns}node")
        edges = graph.findall(f"{ns}edge")
        assert len(nodes) == 2
        assert len(edges) == 1
        node_ids = {n.attrib["id"] for n in nodes}
        assert f"n{ids['a']}" in node_ids
        assert f"n{ids['b']}" in node_ids
        edge = edges[0]
        assert edge.attrib["source"] == f"n{ids['a']}"
        assert edge.attrib["target"] == f"n{ids['b']}"

    def test_xml_escapes_special_characters_in_label(self, isolated_db):
        import knowledge_graph as kg
        kg.upsert_node("learning", "L1", 'A "label" with <html> & special')
        from knowledge_graph import export_to_graphml
        xml = export_to_graphml()
        # Must still parse — the escape is correct.
        ET.fromstring(xml)
        # Raw HTML must NOT appear unescaped.
        assert "<html>" not in xml
        assert "&lt;html&gt;" in xml


# ── Multi-edge graph + multiple relations on same source ─────────────────


class TestMultiEdgeExport:
    def test_node_collects_multiple_relations(self, isolated_db):
        ids = _seed_kg(multi_edge=True)
        from knowledge_graph import export_to_jsonld
        payload = export_to_jsonld()
        assert payload["node_count"] == 3
        assert payload["edge_count"] == 2

        # Decision node should declare nexo:affects with the entity as target.
        decision = next(
            n for n in payload["@graph"] if n["@id"] == f"nexo:node:{ids['b']}"
        )
        assert "nexo:affects" in decision
        assert decision["nexo:affects"][0]["target"] == f"nexo:node:{ids['c']}"


# ── Bitemporal as_of historical query ─────────────────────────────────────


class TestAsOfHistoricalSnapshot:
    def test_as_of_in_past_returns_zero_when_kg_empty_then(self, isolated_db):
        ids = _seed_kg()
        from knowledge_graph import export_to_jsonld
        # An as_of before any edge was created → 0 edges in snapshot.
        payload = export_to_jsonld(as_of="2020-01-01T00:00:00")
        assert payload["snapshot"] == "2020-01-01T00:00:00"
        assert payload["edge_count"] == 0

    def test_as_of_in_future_returns_active_snapshot(self, isolated_db):
        ids = _seed_kg()
        from knowledge_graph import export_to_jsonld
        payload = export_to_jsonld(as_of="2099-12-31T23:59:59")
        assert payload["edge_count"] == 1


# ── MCP tool handler shape ────────────────────────────────────────────────


class TestKgExportTool:
    def test_handler_jsonld_returns_string_dict(self, isolated_db):
        _seed_kg()
        from plugins.knowledge_graph_tools import handle_kg_export
        out = handle_kg_export(format="jsonld")
        payload = json.loads(out)
        assert payload["@type"] == "nexo:KnowledgeGraphSnapshot"
        assert payload["node_count"] == 2

    def test_handler_graphml_returns_xml_string(self, isolated_db):
        _seed_kg()
        from plugins.knowledge_graph_tools import handle_kg_export
        out = handle_kg_export(format="graphml")
        assert out.startswith("<?xml")
        ET.fromstring(out)  # parses

    def test_handler_unsupported_format_returns_error(self, isolated_db):
        from plugins.knowledge_graph_tools import handle_kg_export
        out = handle_kg_export(format="rdf")
        payload = json.loads(out)
        assert payload["ok"] is False
        assert "unsupported format" in payload["error"]
