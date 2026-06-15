"""Tests for the Ola 1 memory eval harness.

These assert that the harness *works and is reproducible*, NOT that recall is
high. They run offline (the conftest isolated_db fixture sets
NEXO_SKIP_COGNITIVE_MODEL_DOWNLOAD=1) so they are deterministic and CI-safe:
the deterministic fallback embedding makes both the metrics module and the
end-to-end retrieval path produce identical numbers on every run.

What is verified:
  - benchmarks/metrics.py math is correct on hand-built fixtures.
  - The golden set on disk is byte-reproducible (hash round-trips).
  - Seeding the golden corpus into the isolated DB and running the REAL
    memory_search produces metrics with valid shape and ranges.
  - The lexical no-regression family still scores recall@5 == 1.0 offline.
  - Two harness runs over the same fixture produce identical metrics.
  - The eval_runs table (migration 85) exists and accepts run rows.
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH_DIR = os.path.join(REPO_ROOT, "benchmarks")


@pytest.fixture(scope="module")
def metrics_mod():
    if BENCH_DIR not in sys.path:
        sys.path.insert(0, BENCH_DIR)
    return importlib.import_module("metrics")


@pytest.fixture(scope="module")
def golden_mod():
    if BENCH_DIR not in sys.path:
        sys.path.insert(0, BENCH_DIR)
    return importlib.import_module("golden.generate")


# ── metrics math ────────────────────────────────────────────────────────────


def test_recall_at_k_basic(metrics_mod):
    # relevant in top-2 of 3 retrieved: recall@1=0 (first is wrong),
    # recall@2=1.0 (single relevant found).
    ranked = ["a", "b", "c"]
    assert metrics_mod.recall_at_k(ranked, ["b"], 1) == 0.0
    assert metrics_mod.recall_at_k(ranked, ["b"], 2) == 1.0
    assert metrics_mod.recall_at_k(ranked, ["b", "c"], 3) == 1.0
    assert metrics_mod.recall_at_k(ranked, ["b", "z"], 3) == 0.5


def test_recall_empty_relevant_is_zero(metrics_mod):
    assert metrics_mod.recall_at_k(["a"], [], 5) == 0.0


def test_reciprocal_rank(metrics_mod):
    assert metrics_mod.reciprocal_rank(["a", "b", "c"], ["b"]) == pytest.approx(0.5)
    assert metrics_mod.reciprocal_rank(["a", "b", "c"], ["z"]) == 0.0


def test_average_precision(metrics_mod):
    # relevant at ranks 1 and 3: AP = (1/1 + 2/3) / 2.
    ap = metrics_mod.average_precision(["a", "x", "b"], ["a", "b"])
    assert ap == pytest.approx((1.0 + 2.0 / 3.0) / 2.0, abs=1e-6)


def test_abstention_correct(metrics_mod):
    assert metrics_mod.abstention_correct([], [], 5) == 1.0
    assert metrics_mod.abstention_correct(["a"], [], 5) == 0.0
    # non-abstention query returns None so it is excluded from the abstention agg
    assert metrics_mod.abstention_correct(["a"], ["a"], 5) is None


def test_ranked_dedup_preserves_first(metrics_mod):
    # duplicate uid (FTS + vector surfaced same doc) counts once at best rank
    assert metrics_mod.reciprocal_rank(["a", "a", "b"], ["a"]) == pytest.approx(1.0)


def test_aggregate_shape_and_ranges(metrics_mod):
    per_query = [
        {"ranked": ["a", "b"], "relevant": ["a"]},
        {"ranked": ["x", "y"], "relevant": ["y"]},
        {"ranked": [], "relevant": []},  # abstention, correct
        {"ranked": ["z"], "relevant": []},  # abstention, wrong
    ]
    agg = metrics_mod.aggregate(per_query, ks=(1, 5, 10))
    assert agg["n_queries"] == 4
    assert agg["n_answerable"] == 2
    assert agg["n_abstention"] == 2
    for key in ("recall@1", "recall@5", "recall@10", "mrr", "map"):
        assert 0.0 <= agg[key] <= 1.0
    assert agg["abstention_accuracy"] == pytest.approx(0.5)


# ── golden set reproducibility ────────────────────────────────────────────────


def test_golden_hash_reproducible(golden_mod):
    assert golden_mod.check_on_disk() is True
    observations, queries, manifest = golden_mod.load_golden()
    recomputed = golden_mod.compute_hash(observations, queries)
    assert recomputed == manifest["fixture_hash"]
    assert manifest["n_queries"] >= 30
    intents = {q["intent"] for q in queries}
    assert {"paraphrase", "lexical", "abstention"} <= intents


def test_golden_generation_is_deterministic(golden_mod):
    obs1, q1 = golden_mod.build_records()
    obs2, q2 = golden_mod.build_records()
    assert golden_mod.compute_hash(obs1, q1) == golden_mod.compute_hash(obs2, q2)


# ── end-to-end harness over the REAL retrieval (offline, isolated DB) ─────────


def _seed_and_query(golden_mod, metrics_mod):
    """Seed the golden corpus into the isolated DB and run real memory_search."""
    import db
    from memory_retrieval import memory_search

    observations, queries, _manifest = golden_mod.load_golden()
    for obs in observations:
        db.upsert_memory_observation(obs)

    per_query = []
    for q in queries:
        result = memory_search(q["text"], limit=10, process_queue=False)
        ranked = [c.get("uid") for c in result.get("candidates", []) if c.get("uid")]
        per_query.append(
            {
                "id": q["id"],
                "intent": q["intent"],
                "ranked": ranked,
                "relevant": q.get("relevant_uids") or [],
            }
        )
    return per_query, metrics_mod.aggregate(per_query, ks=(1, 5, 10))


def test_harness_produces_valid_metrics(isolated_db, golden_mod, metrics_mod):
    _per_query, agg = _seed_and_query(golden_mod, metrics_mod)
    # Valid shape + ranges — NOT a quality assertion.
    assert agg["n_queries"] >= 30
    assert agg["n_answerable"] == 20
    assert agg["n_abstention"] == 10
    for key in ("recall@1", "recall@5", "recall@10", "mrr", "map"):
        assert 0.0 <= agg[key] <= 1.0
    # Monotonic recall: recall@1 <= recall@5 <= recall@10 by construction.
    assert agg["recall@1"] <= agg["recall@5"] <= agg["recall@10"]


def test_harness_is_deterministic(isolated_db, golden_mod, metrics_mod):
    _pq1, agg1 = _seed_and_query(golden_mod, metrics_mod)
    # Re-seed (idempotent upsert) and re-run: identical numbers.
    _pq2, agg2 = _seed_and_query(golden_mod, metrics_mod)
    assert agg1 == agg2


def test_lexical_no_regression(isolated_db, golden_mod, metrics_mod):
    per_query, _agg = _seed_and_query(golden_mod, metrics_mod)
    lexical = [q for q in per_query if q["intent"] == "lexical"]
    lex_agg = metrics_mod.aggregate(lexical, ks=(1, 5, 10))
    # The lexical/FTS path must keep acing exact-token queries even offline.
    assert lex_agg["recall@5"] == 1.0


# ── eval_runs persistence (migration 85) ──────────────────────────────────────


def test_eval_runs_table_exists_and_accepts_rows(isolated_db):
    import db

    conn = db.get_db()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(eval_runs)").fetchall()}
    assert {"suite", "metric", "value", "ola1_enabled", "model_warm"} <= cols

    conn.execute(
        "INSERT INTO eval_runs (suite, metric, value, ola1_enabled, model_warm) "
        "VALUES (?, ?, ?, ?, ?)",
        ("ola1", "recall@5", 0.85, 1, 0),
    )
    conn.commit()
    row = conn.execute(
        "SELECT suite, metric, value FROM eval_runs WHERE suite = 'ola1' AND metric = 'recall@5'"
    ).fetchone()
    assert row["value"] == pytest.approx(0.85)
