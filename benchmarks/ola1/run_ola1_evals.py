#!/usr/bin/env python3
"""Ola 1 memory eval harness — recall@k / MRR baseline over the REAL retrieval.

What it does
------------
1. Seeds the deterministic golden corpus (``benchmarks/golden/``) into an
   ISOLATED cognitive DB under ``/tmp`` (never ``~/.nexo`` production).
2. Runs every golden query through the REAL fused retrieval path,
   ``memory_retrieval.memory_search()`` (FTS + vector fusion — the Ola 1 code).
3. Scores recall@1/5/10, MRR, MAP, abstention accuracy with
   ``benchmarks/metrics.py`` (pure-python, no models), and measures how many
   correct hits came ONLY from the vector signal (the Ola 1 semantic gain).
4. Emits a JSON baseline to stdout (and optionally a file). Two runs produce
   the same numbers — it is deterministic.

Embedding modes
---------------
- ``--mode offline`` (default): ``NEXO_SKIP_COGNITIVE_MODEL_DOWNLOAD=1`` so
  ``cognitive.embed()`` returns the deterministic SHA-256 fallback vector. This
  exercises the full FTS+vector *pipeline* deterministically and offline, but
  the fallback vectors are NOT semantically meaningful, so ``vector_only_hits``
  here measures plumbing, not model quality. Good for CI / reproducibility.
- ``--mode warm``: loads the real ``bge`` embedding model (downloads on first
  run). Then ``vector_only_hits`` and the paraphrase recall reflect TRUE
  semantic gain. Slower; intended for Deep Sleep / nightly, not CI.

Isolation is set up BEFORE importing db/cognitive, mirroring tests/conftest.py
and benchmarks/locomo/run_benchmark.py.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone

# ── Paths ───────────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(REPO_ROOT, "src")
GOLDEN_DIR = os.path.join(REPO_ROOT, "benchmarks", "golden")


def _bootstrap_isolation(mode: str) -> str:
    """Point all DB/runtime env at a fresh /tmp dir BEFORE importing db/cognitive.

    Returns the isolated NEXO_HOME path. Must run before any ``import db`` /
    ``import cognitive`` so the module-level path resolution picks up /tmp.
    """
    bench_home = tempfile.mkdtemp(prefix="nexo-ola1-evals-")
    os.environ["NEXO_HOME"] = bench_home
    os.environ["NEXO_TEST_DB"] = os.path.join(bench_home, "nexo.db")
    os.environ["NEXO_COGNITIVE_DB"] = os.path.join(bench_home, "cognitive.db")
    os.environ["NEXO_LOCAL_CONTEXT_DB"] = os.path.join(bench_home, "local_context.db")
    os.environ["NEXO_SKIP_FS_INDEX"] = "1"
    os.environ["NEXO_SKIP_LEARNING_COGNITIVE_INGEST"] = "1"
    # Offline => deterministic fallback embedding. Warm => real model.
    if mode == "offline":
        os.environ["NEXO_SKIP_COGNITIVE_MODEL_DOWNLOAD"] = "1"
    else:
        os.environ.pop("NEXO_SKIP_COGNITIVE_MODEL_DOWNLOAD", None)
    # Keep repo src first so we never pick up an installed runtime build.
    sys.path[:] = [p for p in sys.path if p != SRC]
    sys.path.insert(0, SRC)
    return bench_home


def _init_schema():
    """Create the isolated DB and run all migrations."""
    import db._core as db_core

    # Make sure the module-level DB_PATH reflects our env (it was resolved at
    # import time; re-resolve and reset any cached connection).
    db_core.close_db()
    db_core.DB_PATH = db_core._resolve_db_path()
    os.makedirs(os.path.dirname(db_core.DB_PATH), exist_ok=True)

    import cognitive._core as cog_core

    cog_core.COGNITIVE_DB = os.environ["NEXO_COGNITIVE_DB"]
    cog_core._conn = None

    from db._core import init_db
    from db._schema import run_migrations

    init_db()
    run_migrations()


def _warm_model_if_needed(mode: str) -> bool:
    """In warm mode, force the real embedding model to load. Returns warm flag.

    Returns True when query/observation embedding will use real semantics
    (warm model OR — in offline mode — the deterministic fallback that the
    retrieval path treats as 'warm'). The metric ``model_warm`` records whether
    the numbers reflect the real model (True) or the offline fallback (False).
    """
    import cognitive._core as cog_core

    if mode == "offline":
        # Fallback path is deterministic but NOT real semantics.
        return False
    # Force a real load so seeding precomputes real embeddings.
    cog_core.embed("warmup")
    return getattr(cog_core, "_model", None) is not None


def _seed_corpus(observations: list[dict]) -> int:
    """Seed golden observations via the REAL upsert (precomputes embeddings).

    ``upsert_memory_observation`` writes the row AND, when the model is warm
    (or offline-fallback active), stores the precomputed embedding — so the
    vector fusion path has data to scan. Returns count seeded.
    """
    from db import upsert_memory_observation

    seeded = 0
    for obs in observations:
        result = upsert_memory_observation(obs)
        if result.get("ok"):
            seeded += 1
    return seeded


def _run_queries(queries: list[dict], limit: int = 10) -> list[dict]:
    """Run each golden query through the REAL fused memory_search.

    Returns per-query dicts with the ranked uid list, vector_score per hit, the
    ground-truth relevant_uids, intent and elapsed_ms.
    """
    from memory_retrieval import memory_search

    per_query: list[dict] = []
    for q in queries:
        t0 = time.perf_counter()
        result = memory_search(
            q["text"],
            project_hint="",
            limit=limit,
            # Queue is empty in the bench; skip the per-call drain for speed and
            # determinism (we seed observations directly, not via events).
            process_queue=False,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        candidates = result.get("candidates") or []
        ranked = [c.get("uid") for c in candidates if c.get("uid")]
        vector_scores = {
            c.get("uid"): float(c.get("vector_score") or 0.0)
            for c in candidates
            if c.get("uid")
        }
        per_query.append(
            {
                "id": q["id"],
                "intent": q["intent"],
                "ranked": ranked,
                "relevant": q.get("relevant_uids") or [],
                "vector_scores": vector_scores,
                "has_evidence": bool(result.get("has_evidence")),
                "elapsed_ms": round(elapsed_ms, 2),
            }
        )
    return per_query


def _vector_only_hits(per_query: list[dict]) -> dict:
    """Count correct top-k hits that carried a positive vector_score.

    A relevant uid in the results with ``vector_score > 0`` means the semantic
    (vector) signal contributed to surfacing/ranking it — the Ola 1 fusion
    gain. We report, over answerable queries, how many of the first relevant
    hits had vector support.
    """
    answerable = [q for q in per_query if q["relevant"]]
    relevant_hits = 0
    vector_supported = 0
    for q in answerable:
        rel = set(q["relevant"])
        for uid in q["ranked"][:10]:
            if uid in rel:
                relevant_hits += 1
                if q["vector_scores"].get(uid, 0.0) > 0:
                    vector_supported += 1
                break  # count the first relevant hit per query
    pct = (vector_supported / relevant_hits) if relevant_hits else 0.0
    return {
        "answerable_queries": len(answerable),
        "relevant_first_hits": relevant_hits,
        "vector_supported_hits": vector_supported,
        "vector_only_pct": round(pct, 4),
    }


def run(mode: str = "offline", limit: int = 10) -> dict:
    """Full harness run. Returns the baseline dict (also JSON-serialisable)."""
    # Local import after isolation bootstrap so paths resolve to /tmp.
    sys.path.insert(0, os.path.join(REPO_ROOT, "benchmarks"))
    from golden.generate import load_golden, compute_hash  # type: ignore
    import metrics  # type: ignore

    observations, queries, manifest = load_golden()
    recomputed = compute_hash(observations, queries)
    fixture_ok = recomputed == manifest.get("fixture_hash")

    _init_schema()
    model_warm = _warm_model_if_needed(mode)
    seeded = _seed_corpus(observations)
    per_query = _run_queries(queries, limit=limit)

    agg = metrics.aggregate(per_query, ks=(1, 5, 10))
    semantic = _vector_only_hits(per_query)

    # Per-intent recall@5 for the no-regression vs semantic-gain breakdown.
    by_intent: dict[str, dict] = {}
    for intent in ("paraphrase", "lexical", "abstention"):
        subset = [q for q in per_query if q["intent"] == intent]
        if not subset:
            continue
        by_intent[intent] = metrics.aggregate(subset, ks=(1, 5, 10))

    latencies = sorted(q["elapsed_ms"] for q in per_query)
    p50 = latencies[len(latencies) // 2] if latencies else 0.0
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0.0

    return {
        "suite": "ola1",
        "mode": mode,
        "model_warm": model_warm,
        "fixture_hash": manifest.get("fixture_hash"),
        "fixture_reproducible": fixture_ok,
        "case_set_id": manifest.get("case_set_id"),
        "case_set_version": manifest.get("case_set_version"),
        "seeded_observations": seeded,
        "metrics": agg,
        "semantic_gain": semantic,
        "by_intent": by_intent,
        "latency_ms": {"p50": round(p50, 2), "p95": round(p95, 2)},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def persist_eval_run(baseline: dict, db_already_isolated: bool = True) -> int:
    """Persist run-level metrics into the ``eval_runs`` table. Returns rows written.

    Writes one row per top-level metric so the table is a queryable time series
    (recall@1/5/10, mrr, map, vector_only_pct, abstention_accuracy). Uses the
    SAME isolated DB the harness already opened — never production.
    """
    from db._core import get_db

    conn = get_db()
    rows = []
    m = baseline["metrics"]
    flat = {
        "recall@1": m.get("recall@1"),
        "recall@5": m.get("recall@5"),
        "recall@10": m.get("recall@10"),
        "mrr": m.get("mrr"),
        "map": m.get("map"),
        "vector_only_pct": baseline["semantic_gain"].get("vector_only_pct"),
        "abstention_accuracy": m.get("abstention_accuracy"),
    }
    for metric, value in flat.items():
        if value is None:
            continue
        conn.execute(
            """
            INSERT INTO eval_runs
                (suite, case_set_id, case_set_version, fixture_hash, metric, value,
                 ola1_enabled, model_warm)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                baseline["suite"],
                baseline["case_set_id"],
                baseline["case_set_version"],
                baseline["fixture_hash"],
                metric,
                float(value),
                1,
                1 if baseline["model_warm"] else 0,
            ),
        )
        rows.append(metric)
    conn.commit()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ola 1 memory eval harness")
    parser.add_argument("--mode", choices=["offline", "warm"], default="offline")
    parser.add_argument("--limit", type=int, default=10, help="top-k retrieved per query")
    parser.add_argument("--out", default="", help="optional path to write the JSON baseline")
    parser.add_argument("--persist", action="store_true", help="also write rows to eval_runs (isolated DB)")
    parser.add_argument("--keep-home", action="store_true", help="do not delete the /tmp NEXO_HOME after run")
    args = parser.parse_args()

    bench_home = _bootstrap_isolation(args.mode)
    try:
        baseline = run(mode=args.mode, limit=args.limit)
        if args.persist:
            baseline["eval_runs_rows_written"] = persist_eval_run(baseline)
        text = json.dumps(baseline, indent=2, sort_keys=True)
        print(text)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        return 0
    finally:
        if not args.keep_home and os.path.isdir(bench_home):
            shutil.rmtree(bench_home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
