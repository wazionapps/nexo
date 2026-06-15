"""Ola 1 — kg_neighbors pre-answer source: read the KG/causal graph at answer time
(task_close writes edges in 7.32.0; nothing read them in pre_answer until now)."""

import sys

import pytest


def test_kg_neighbors_step_in_expected_plans():
    import pre_answer_router as par
    for intent in ("prior_work", "modify_existing", "identity_authorship",
                   "live_state_claim", "runtime_diagnosis"):
        assert "kg_neighbors" in par._SOURCE_PLANS[intent].source_names(), intent
    # Kept out of the lean / instant plans.
    assert "kg_neighbors" not in par._SOURCE_PLANS["memory_question"].source_names()
    assert "kg_neighbors" not in par._SOURCE_PLANS["general"].source_names()
    assert "kg_neighbors" in par.default_source_adapters()


def test_kg_neighbors_returns_neighbors_for_known_file():
    import knowledge_graph as kg
    import pre_answer_router as par
    kg.upsert_edge(source_type="change", source_ref="change:1", relation="touched",
                   target_type="file", target_ref="src/x.py")
    req = par.SourceRequest(query="por que toque src/x.py", intent="prior_work",
                            area="nexo", files="src/x.py")
    res = par._source_kg_neighbors(req)
    assert res.has_evidence
    assert "touched" in res.rendered
    assert res.result_count >= 1


def test_kg_neighbors_includes_causal_edges():
    import causal_graph
    import pre_answer_router as par
    causal_graph.upsert_active_edge(
        source_type="file", source_ref="src/y.py", relation="causal:verified_by",
        target_type="test", target_ref="tests/test_y.py",
        reason_public="verified by y tests", evidence_refs=["pytest:y"],
        producer="test", project_key="nexo", confidence=0.95)
    req = par.SourceRequest(query="", intent="prior_work", area="nexo", files="src/y.py")
    res = par._source_kg_neighbors(req)
    assert res.has_evidence
    assert ("pytest:y" in res.evidence_refs) or ("causal:verified_by" in res.rendered)


def test_kg_neighbors_empty_when_no_refs():
    import pre_answer_router as par
    req = par.SourceRequest(query="que hice ayer", intent="prior_work", area="nexo", files="")
    res = par._source_kg_neighbors(req)
    assert not res.has_evidence


def test_kg_neighbors_import_failure_fails_open(monkeypatch):
    import pre_answer_router as par
    monkeypatch.setitem(sys.modules, "knowledge_graph", None)
    req = par.SourceRequest(query="src/x.py", intent="prior_work", area="nexo", files="src/x.py")
    res = par._source_kg_neighbors(req)
    assert res.skipped is True
    assert res.aborted_reason == "source_error"
