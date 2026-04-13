"""Tests for Knowledge Graph operations."""


def test_upsert_and_get_node():
    """Create a node and retrieve it."""
    import cognitive
    cognitive._get_db()  # Ensure KG tables exist

    import knowledge_graph as kg
    node_id = kg.upsert_node("area", "area:test", "Test Area", {"color": "blue"})
    assert node_id > 0

    node = kg.get_node("area", "area:test")
    assert node is not None
    assert node["label"] == "Test Area"

    # Upsert same node should return same id (update)
    node_id2 = kg.upsert_node("area", "area:test", "Test Area Updated")
    assert node_id2 == node_id

    node2 = kg.get_node("area", "area:test")
    assert node2["label"] == "Test Area Updated"


def test_upsert_and_get_edge():
    """Create nodes and edges, verify traversal."""
    import cognitive
    cognitive._get_db()

    import knowledge_graph as kg
    kg.upsert_node("learning", "learning:1", "L1")
    kg.upsert_node("file", "file:foo.py", "foo.py")

    result = kg.upsert_edge(
        "learning", "learning:1", "touched",
        "file", "file:foo.py", weight=1.0,
    )
    assert result["action"] == "ADD"

    # Same edge again → NOOP
    result2 = kg.upsert_edge(
        "learning", "learning:1", "touched",
        "file", "file:foo.py", weight=1.0,
    )
    assert result2["action"] == "NOOP"

    # Different weight → UPDATE (closes old, creates new)
    result3 = kg.upsert_edge(
        "learning", "learning:1", "touched",
        "file", "file:foo.py", weight=0.5,
    )
    assert result3["action"] == "UPDATE"


def test_neighbors():
    """Get direct neighbors of a node."""
    import cognitive
    cognitive._get_db()

    import knowledge_graph as kg
    kg.upsert_node("area", "area:myproject", "MyProject")
    kg.upsert_node("learning", "learning:10", "L10")
    kg.upsert_node("learning", "learning:11", "L11")
    kg.upsert_edge("learning", "learning:10", "belongs_to", "area", "area:myproject")
    kg.upsert_edge("learning", "learning:11", "belongs_to", "area", "area:myproject")

    area_node = kg.get_node("area", "area:myproject")
    neighbors = kg.get_neighbors(area_node["id"])
    assert len(neighbors) == 2


def test_traverse():
    """BFS traversal from a node."""
    import cognitive
    cognitive._get_db()

    import knowledge_graph as kg
    # A → B → C chain
    kg.upsert_node("area", "area:a", "A")
    kg.upsert_node("file", "file:b", "B")
    kg.upsert_node("change", "change:c", "C")
    kg.upsert_edge("area", "area:a", "has", "file", "file:b")
    kg.upsert_edge("file", "file:b", "modified_by", "change", "change:c")

    a_node = kg.get_node("area", "area:a")
    result = kg.traverse(a_node["id"], max_depth=2)
    node_ids = {n["id"] for n in result["nodes"]}
    # Should reach A, B, and C within depth 2
    assert len(node_ids) == 3


def test_shortest_path():
    """Find shortest path between two nodes."""
    import cognitive
    cognitive._get_db()

    import knowledge_graph as kg
    kg.upsert_node("area", "area:x", "X")
    kg.upsert_node("file", "file:y", "Y")
    kg.upsert_node("change", "change:z", "Z")
    kg.upsert_edge("area", "area:x", "has", "file", "file:y")
    kg.upsert_edge("file", "file:y", "modified_by", "change", "change:z")

    x = kg.get_node("area", "area:x")
    z = kg.get_node("change", "change:z")
    path = kg.shortest_path(x["id"], z["id"])
    assert path is not None
    assert len(path) == 3  # X → Y → Z


def test_delete_edge():
    """Soft-delete an edge (set valid_until)."""
    import cognitive
    cognitive._get_db()

    import knowledge_graph as kg
    kg.upsert_node("area", "area:del", "Del")
    kg.upsert_node("file", "file:del", "Del File")
    kg.upsert_edge("area", "area:del", "has", "file", "file:del")

    deleted = kg.delete_edge("area", "area:del", "has", "file", "file:del")
    assert deleted is True

    # After deletion, no active neighbors
    node = kg.get_node("area", "area:del")
    neighbors = kg.get_neighbors(node["id"], active_only=True)
    assert len(neighbors) == 0


def test_stats():
    """Stats should return valid counts."""
    import cognitive
    cognitive._get_db()

    import knowledge_graph as kg
    kg.upsert_node("area", "area:stats", "Stats Test")
    s = kg.stats()
    assert s["nodes"] >= 1
    assert isinstance(s["edges_active"], int)
    assert isinstance(s["node_types"], dict)
