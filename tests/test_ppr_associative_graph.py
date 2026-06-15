"""Ola 2 — Personalized PageRank associative graph (src/ppr.py + adapter).

All tests run against the test-isolated cognitive DB (conftest sets
NEXO_COGNITIVE_DB to a tmp path; never ~/.nexo prod). Synthetic KGs are seeded
through the public knowledge_graph API.
"""

import sys

import pytest


def _seed_hub_graph():
    """Build a hub-heavy synthetic KG that traps a naive PPR.

    Topology:
      file:A --touched--> learning:L  (the semantically right answer chain)
      learning:L --belongs_to_area--> area:general  (the HUB)
      50 unrelated diaries D0..D49 --belongs_to_area--> area:general
    A hub-naive PPR seeded on file:A would surface area:general and its 50
    siblings; a column-stochastic + hub-down-weighted PPR must NOT.
    """
    import knowledge_graph as kg

    kg.upsert_edge("file", "file:A", "touched", "learning", "learning:L",
                   weight=1.0, confidence=1.0)
    kg.upsert_edge("learning", "learning:L", "belongs_to_area", "area", "area:general",
                   weight=1.0, confidence=1.0)
    kg.upsert_edge("file", "file:A", "applies_to_file", "learning", "learning:M",
                   weight=1.0, confidence=1.0)
    for i in range(50):
        kg.upsert_edge("diary", f"diary:{i}", "belongs_to_area", "area", "area:general",
                       weight=1.0, confidence=1.0)
    return kg


def _node_id(kg, ntype, nref):
    node = kg.get_node(ntype, nref)
    assert node, f"missing node {ntype}:{nref}"
    return int(node["id"])


# --------------------------------------------------------------------------- #
# PPR core
# --------------------------------------------------------------------------- #

def test_ppr_deterministic():
    import ppr
    kg = _seed_hub_graph()
    a = _node_id(kg, "file", "file:A")
    g = ppr.build_graph()
    r1 = ppr.push_ppr(g, {a: 1.0})
    r2 = ppr.push_ppr(g, {a: 1.0})
    assert r1 == r2  # identical graph + seeds -> identical estimate
    # Mass concentrates on the seed and decays away from it.
    assert r1[a] > 0
    assert r1[a] == max(r1.values())


def test_ppr_anti_hub_normalized_transition():
    """Column-stochastic transition must stop the 50 hub-SIBLING diaries (which
    share area:general) from flooding the ranking.

    The hub node itself (a true 1-hop-via-chain neighbour) may legitimately
    appear; the failure mode we guard against is its hundreds of *siblings*
    leaking back across the hub and drowning the real answer — exactly what an
    un-normalised PPR does."""
    import ppr
    kg = _seed_hub_graph()
    a = _node_id(kg, "file", "file:A")
    learning_l = _node_id(kg, "learning", "learning:L")
    learning_m = _node_id(kg, "learning", "learning:M")
    diary_ids = {_node_id(kg, "diary", f"diary:{i}") for i in range(50)}

    scored = {r.node_id: r.score for r in ppr.rank_related({a: 1.0}, top_n=60)}
    # The semantically correct neighbours dominate.
    assert scored.get(learning_l, 0) > 0 and scored.get(learning_m, 0) > 0
    real_min = min(scored[learning_l], scored[learning_m])
    # Every hub-sibling diary must score an order of magnitude below the real
    # neighbours: the column-stochastic transition splits the hub's outgoing
    # mass across all 50 siblings (1/50 each) instead of broadcasting full mass
    # to each, so no sibling can leak enough to flood the answer.
    for d in diary_ids:
        assert scored.get(d, 0.0) < real_min / 10, "hub-sibling leaked too much mass"

    # No individual sibling outranks a real neighbour, and the two real
    # neighbours together still hold more mass than all 50 siblings combined —
    # the defining property of a hub-safe (column-stochastic) walk: a high-degree
    # node dilutes mass across its targets instead of broadcasting it.
    sibling_max = max((scored.get(d, 0.0) for d in diary_ids), default=0.0)
    real_mass = scored[learning_l] + scored[learning_m]
    sibling_mass = sum(scored.get(d, 0.0) for d in diary_ids)
    assert sibling_max < real_min, (sibling_max, real_min)
    assert sibling_mass < real_mass, (sibling_mass, real_mass)


def test_ppr_column_stochastic_splits_hub_mass():
    """Direct property: a hub's outgoing transition probabilities sum to 1 and
    are split across its targets (column-stochastic), so a high-degree node
    cannot broadcast undiluted mass."""
    import ppr
    kg = _seed_hub_graph()
    gen = _node_id(kg, "area", "area:general")
    g = ppr.build_graph()
    # area:general has 51 incoming edges and 0 outgoing -> its only transitions
    # are reverse edges, and they MUST sum to 1.0 (normalised), each tiny.
    trans = list(g.transitions(gen, reverse_fraction=ppr.DEFAULT_REVERSE_FRACTION))
    assert trans, "hub should have reverse transitions"
    total = sum(p for _, p in trans)
    assert abs(total - 1.0) < 1e-9, total
    # No single reverse-target gets more than a small slice (51 contenders).
    assert max(p for _, p in trans) < 0.1


def test_ppr_multi_hop_beats_one_hop():
    """A node reachable only at >=2 hops is recovered by PPR but not by the
    bounded 1-hop fan-out."""
    import ppr
    import knowledge_graph as kg
    # chain: file:A -> learning:L -> decision:D -> change:C  (2-3 hops from A)
    kg.upsert_edge("file", "file:A", "touched", "learning", "learning:L")
    kg.upsert_edge("learning", "learning:L", "belongs_to", "decision", "decision:D")
    kg.upsert_edge("decision", "decision:D", "ops:produced", "change", "change:C")
    a = _node_id(kg, "file", "file:A")
    decision_d = _node_id(kg, "decision", "decision:D")
    change_c = _node_id(kg, "change", "change:C")

    one_hop = {
        int(nb["target_id"]) if int(nb["source_id"]) == a else int(nb["source_id"])
        for nb in kg.get_neighbors(a, active_only=True)
    }
    ranked_ids = {r.node_id for r in ppr.rank_related({a: 1.0}, top_n=12)}
    # decision:D (2 hops) and change:C (3 hops) are NOT 1-hop neighbours...
    assert decision_d not in one_hop
    assert change_c not in one_hop
    # ...but PPR reaches at least the 2-hop node.
    assert decision_d in ranked_ids


def test_ppr_fail_open_on_graph_error(monkeypatch):
    import ppr

    def boom(**_):
        raise RuntimeError("graph blew up")

    monkeypatch.setattr(ppr, "build_graph", boom)
    out = ppr.rank_related({1: 1.0}, top_n=5)
    assert out == []  # no exception, empty result


def test_ppr_empty_seeds():
    import ppr
    assert ppr.push_ppr(ppr.PPRGraph(), {}) == {}
    assert ppr.rank_related({}, top_n=5) == []


def test_ppr_seed_cap_and_max_push_bounded():
    import ppr
    kg = _seed_hub_graph()
    a = _node_id(kg, "file", "file:A")
    g = ppr.build_graph()
    # A tiny max_push must still return without error and stay bounded.
    est = ppr.push_ppr(g, {a: 1.0}, max_push=1)
    assert isinstance(est, dict)
    assert len(est) <= 60  # cannot touch more than the small graph


# --------------------------------------------------------------------------- #
# Adapter integration
# --------------------------------------------------------------------------- #

def test_adapter_registered_and_in_plans():
    import pre_answer_router as par
    assert "associative_graph" in par.default_source_adapters()
    assert "associative_graph" in par._SOURCE_PLANS["prior_work"].source_names()
    assert "associative_graph" in par._SOURCE_PLANS["modify_existing"].source_names()
    # Kept out of lean/instant intents.
    assert "associative_graph" not in par._SOURCE_PLANS["general"].source_names()
    assert "associative_graph" not in par._SOURCE_PLANS["memory_question"].source_names()


def test_adapter_in_canonical_router_sources():
    from pre_answer_runtime import CANONICAL_ROUTER_SOURCES
    from pre_answer_router import default_source_adapters
    assert "associative_graph" in CANONICAL_ROUTER_SOURCES
    # canonical must have a registered adapter (else stripped/discarded silently)
    assert "associative_graph" in default_source_adapters()


def test_adapter_returns_multi_hop_evidence():
    import knowledge_graph as kg
    import pre_answer_router as par
    kg.upsert_edge("file", "file:src/widget.py", "touched", "learning", "learning:42")
    kg.upsert_edge("learning", "learning:42", "belongs_to", "decision", "decision:7")
    req = par.SourceRequest(query="que hicimos con src/widget.py",
                            intent="prior_work", area="nexo", files="src/widget.py")
    res = par._source_associative_graph(req)
    assert res.has_evidence
    assert res.result_count >= 1
    assert all(ref.startswith("kg:node:") for ref in res.evidence_refs)


def test_adapter_empty_when_no_seeds():
    import pre_answer_router as par
    req = par.SourceRequest(query="hola que tal el dia", intent="prior_work",
                            area="nexo", files="")
    res = par._source_associative_graph(req)
    assert not res.has_evidence


def test_adapter_seed_gate_skips_entities_on_generic_query(monkeypatch):
    """A generic query must NOT scan the entities table (gate parity with
    local_context). We assert resolve_entity is never called."""
    import pre_answer_router as par
    import knowledge_graph as kg

    called = {"n": 0}
    import entity_live_profile

    def spy(*a, **k):
        called["n"] += 1
        return {"candidates": []}

    monkeypatch.setattr(entity_live_profile, "resolve_entity", spy)
    req = par.SourceRequest(query="buenos dias", intent="prior_work", area="", files="")
    par._associative_graph_seeds(req, kg, max_seeds=8)
    assert called["n"] == 0, "entities table scanned on a generic query"


def test_adapter_import_failure_fails_open(monkeypatch):
    import pre_answer_router as par
    monkeypatch.setitem(sys.modules, "ppr", None)
    req = par.SourceRequest(query="src/x.py", intent="prior_work", area="nexo", files="src/x.py")
    res = par._source_associative_graph(req)
    assert res.skipped is True
    assert res.aborted_reason == "source_error"


def test_adapter_emits_cacheable_kg_node_refs():
    """Refs must be kg:node:<id> (cacheable via resolution_cache global
    watermark), NOT kg:ppr: (which would be untrackable)."""
    import knowledge_graph as kg
    import pre_answer_router as par
    kg.upsert_edge("file", "file:src/cacheable.py", "touched", "learning", "learning:99")
    req = par.SourceRequest(query="src/cacheable.py", intent="prior_work",
                            area="nexo", files="src/cacheable.py")
    res = par._source_associative_graph(req)
    if res.evidence_refs:
        assert all(r.startswith("kg:node:") for r in res.evidence_refs)
        assert not any(r.startswith("kg:ppr:") for r in res.evidence_refs)


# --------------------------------------------------------------------------- #
# Per-process graph cache + cold-start pre-warm (Ola 2 cold-start fix)
# --------------------------------------------------------------------------- #

@pytest.fixture
def fresh_ppr_cache():
    """Reset the module-level graph cache so stats start clean per test."""
    import ppr
    ppr.reset_graph_cache()
    yield ppr
    ppr.reset_graph_cache()


def test_cache_reused_on_second_call_no_rebuild(fresh_ppr_cache):
    """Property (a): the second build_graph-via-cache does NOT rebuild — it
    returns the SAME object, and the build counter stays at 1."""
    ppr = fresh_ppr_cache
    _seed_hub_graph()
    g1 = ppr.get_cached_graph()
    g2 = ppr.get_cached_graph()
    assert g1 is g2, "second call rebuilt instead of reusing the cached graph"
    assert ppr._CACHE_STATS["builds"] == 1, ppr._CACHE_STATS
    assert ppr._CACHE_STATS["hits"] >= 1
    # rank_related (warm) must also reuse it, not rebuild.
    a = _node_id(__import__("knowledge_graph"), "file", "file:A")
    ppr.rank_related({a: 1.0}, top_n=12)
    assert ppr._CACHE_STATS["builds"] == 1, "rank_related rebuilt a warm graph"


def test_cache_warm_is_faster_than_cold(fresh_ppr_cache):
    """Property (a) measured: a warm cached call is materially cheaper than the
    cold build it replaces."""
    import time
    ppr = fresh_ppr_cache
    _seed_hub_graph()

    t0 = time.perf_counter()
    ppr.get_cached_graph()  # cold: builds
    cold_ms = (time.perf_counter() - t0) * 1000.0

    t1 = time.perf_counter()
    ppr.get_cached_graph()  # warm: cache hit
    warm_ms = (time.perf_counter() - t1) * 1000.0

    assert ppr._CACHE_STATS["builds"] == 1
    # Warm path must be a fraction of the cold build (cache hit is a dict lookup
    # + one MAX/COUNT SELECT). Generous bound to stay robust on slow CI.
    assert warm_ms < cold_ms, (cold_ms, warm_ms)


def test_cold_start_adapter_does_not_build_inline_degrades_clean(fresh_ppr_cache, monkeypatch):
    """Property (b): on a COLD cache the adapter must NOT build the full graph
    inline (which would risk the step timeout). It kicks a background pre-warm
    and degrades to 1-hop, returning quickly without raising."""
    import knowledge_graph as kg
    import pre_answer_router as par
    ppr = fresh_ppr_cache

    # multi-hop chain so 1-hop fallback still has something to return
    kg.upsert_edge("file", "file:src/cold.py", "touched", "learning", "learning:cold")
    kg.upsert_edge("learning", "learning:cold", "belongs_to", "decision", "decision:cold")

    # Fail loudly if the adapter builds the graph inline on the cold path.
    def _boom(**_):
        raise AssertionError("adapter built the full graph inline on cold start")
    monkeypatch.setattr(ppr, "build_graph", _boom)

    assert not ppr.cache_is_warm()
    req = par.SourceRequest(query="src/cold.py", intent="prior_work",
                            area="nexo", files="src/cold.py")
    res = par._source_associative_graph(req)  # must not raise
    # Degraded clean: a result (1-hop) with cacheable refs, or empty — never an error.
    assert res.aborted_reason != "timeout"
    if res.evidence_refs:
        assert all(r.startswith("kg:node:") for r in res.evidence_refs)


def test_prewarm_makes_subsequent_query_multi_hop(fresh_ppr_cache):
    """Cold-start contract end to end: query-1 degrades (1-hop), pre-warm builds
    in the background, query-2 gets multi-hop off the warm cache."""
    import knowledge_graph as kg
    import pre_answer_router as par
    ppr = fresh_ppr_cache

    # 2-hop chain: decision:D is only reachable at >=2 hops (not in 1-hop fan-out)
    kg.upsert_edge("file", "file:src/warm.py", "touched", "learning", "learning:warm")
    kg.upsert_edge("learning", "learning:warm", "belongs_to", "decision", "decision:warm")
    decision_d = _node_id(kg, "decision", "decision:warm")

    req = par.SourceRequest(query="src/warm.py", intent="prior_work",
                            area="nexo", files="src/warm.py")

    # query-1: cold -> degrades, triggers pre-warm
    assert not ppr.cache_is_warm()
    par._source_associative_graph(req)

    # Wait for the background pre-warm to finish (bounded).
    import time
    deadline = time.time() + 5.0
    while not ppr.cache_is_warm() and time.time() < deadline:
        time.sleep(0.02)
    assert ppr.cache_is_warm(), "pre-warm did not finish"

    # query-2: warm -> multi-hop reaches the 2-hop decision node.
    res2 = par._source_associative_graph(req)
    ids = set()
    for ref in res2.evidence_refs:
        # ref form: kg:node:<id>
        try:
            ids.add(int(ref.rsplit(":", 1)[1]))
        except (ValueError, IndexError):
            pass
    assert decision_d in ids, "warm query-2 did not surface the 2-hop node"


def test_fingerprint_invalidates_cache_on_kg_change(fresh_ppr_cache):
    """Property (c): adding a KG edge moves the (MAX id, COUNT) fingerprint, so
    the cache rebuilds rather than serving a stale graph. This is the case the
    global change_log watermark would MISS (upsert_edge never writes change_log)."""
    import knowledge_graph as kg
    ppr = fresh_ppr_cache

    _seed_hub_graph()
    fp_before = ppr.kg_fingerprint()
    g1 = ppr.get_cached_graph()
    edges_before = g1.edge_count
    assert ppr._CACHE_STATS["builds"] == 1

    # Mutate the KG (new edge) — change_log is NOT touched by upsert_edge.
    kg.upsert_edge("file", "file:NEW", "touched", "learning", "learning:NEW")
    fp_after = ppr.kg_fingerprint()
    assert fp_after != fp_before, "fingerprint did not move on KG change"

    g2 = ppr.get_cached_graph()
    assert g2 is not g1, "served a STALE graph after the KG changed"
    assert ppr._CACHE_STATS["builds"] == 2, "cache did not rebuild on KG change"
    assert g2.edge_count > edges_before


def test_change_log_watermark_does_not_move_on_kg_edge(fresh_ppr_cache):
    """Documents WHY we use a KG-local fingerprint instead of get_change_watermark:
    a direct kg_edges insert via upsert_edge does NOT advance the global
    change_log watermark, so reusing it would serve a stale graph forever."""
    import knowledge_graph as kg
    try:
        import db
    except Exception:
        pytest.skip("db package unavailable")
    if not hasattr(db, "get_change_watermark"):
        pytest.skip("get_change_watermark not exposed")

    wm_before = db.get_change_watermark()
    kg.upsert_edge("file", "file:WM", "touched", "learning", "learning:WM")
    wm_after = db.get_change_watermark()
    assert wm_after == wm_before, (
        "change_log watermark unexpectedly moved on a KG edge — if this ever "
        "becomes true, get_change_watermark could replace kg_fingerprint"
    )
    # ...but the KG-local fingerprint DID move:
    ppr = fresh_ppr_cache
    fp = ppr.kg_fingerprint()
    assert fp != (0, 0)
