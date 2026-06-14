"""Egress secret filtering for context_query / get_neighbors (Release A / A5).

The entity dossier already drops chunks whose text contains a secret, but the
plain search (context_query chunks + relations) and get_neighbors did NOT
re-check on the way out. Defense-in-depth: never egress a chunk or relation
carrying a secret, even if it somehow slipped past the ingestion gate.
"""

from local_context import api


def test_egress_safe_relations_drops_relations_with_secret_evidence():
    rows = [
        {"relation_id": "r1", "evidence": "reunion con el proveedor sobre la factura", "relation_type": "mentions"},
        {"relation_id": "r2", "evidence": "api token: Bearer abcdefghijklmnop123456789", "relation_type": "mentions"},
        {"relation_id": "r3", "evidence": "", "relation_type": "file_in_folder"},
    ]
    safe = api._egress_safe_relations(rows)
    ids = [r["relation_id"] for r in safe]
    assert "r1" in ids
    assert "r3" in ids
    assert "r2" not in ids, "a relation whose evidence carries a secret must not be returned"


def test_egress_safe_relations_returns_plain_dicts():
    rows = [{"relation_id": "r1", "evidence": "ok", "relation_type": "mentions"}]
    safe = api._egress_safe_relations(rows)
    assert isinstance(safe, list)
    assert safe and isinstance(safe[0], dict)
    assert safe[0]["relation_id"] == "r1"
