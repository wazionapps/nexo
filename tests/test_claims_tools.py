"""Tests for claims/wiki public surface."""


def test_claim_add_and_get_with_evidence_and_freshness():
    import claim_graph

    result = claim_graph.add_claim(
        text="NEXO should preserve critical state before compaction.",
        domain="nexo",
        evidence="Observed repeated context loss during compaction.",
        confidence=0.9,
        freshness_days=14,
        source_type="spec",
        source_id="v4",
    )
    assert result["action"] in {"added", "merged"}

    item = claim_graph.get_claim(result["id"])
    assert item is not None
    assert item["domain"] == "nexo"
    assert item["evidence"] == "Observed repeated context loss during compaction."
    assert item["freshness_state"] in {"fresh", "aging", "stale"}


def test_claim_lint_surfaces_missing_evidence_and_staleness():
    import claim_graph
    db = claim_graph._get_db()

    result = claim_graph.add_claim(
        text="Old unverified claim for lint coverage.",
        domain="nexo",
        evidence="",
        freshness_days=1,
    )
    db.execute(
        "UPDATE claims SET created_at = datetime('now', '-10 days'), updated_at = datetime('now', '-10 days') WHERE id = ?",
        (result["id"],),
    )
    db.commit()

    items = claim_graph.lint_claims(max_age_days=3, limit=20)
    target = next(item for item in items if item["id"] == result["id"])
    assert "missing-evidence" in target["lint_reasons"]
    assert "stale" in target["lint_reasons"]
