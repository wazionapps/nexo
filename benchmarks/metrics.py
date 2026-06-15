#!/usr/bin/env python3
"""Information-retrieval metrics for the NEXO memory eval bench.

Pure Python (numpy optional, only for mean/std aggregation). NO models, NO
network, NO database — this module only consumes ranked id lists and ground
truth (``qrels``), so it runs in CI offline and is fully deterministic.

Vocabulary
----------
- ``ranked``: the ordered list of retrieved ids, best-first (rank 1 = index 0).
- ``relevant``: the set of ground-truth relevant ids for a query (the qrels).
  An empty ``relevant`` set marks an *abstention* query (the correct behaviour
  is to retrieve nothing relevant — see :func:`abstention_correct`).

All functions take a single query's data and return a single float. Aggregate
helpers (``mean``, ``aggregate``) combine per-query results across a run.
"""

from __future__ import annotations

from typing import Iterable, Sequence

try:  # numpy is already a hard dep of the bench, but degrade gracefully.
    import numpy as _np
except Exception:  # pragma: no cover - numpy is present in the repo env
    _np = None


def _as_list(ranked: Iterable[str]) -> list[str]:
    """De-duplicate a ranked id list while preserving first-seen order.

    Retrieval can surface the same underlying observation through both the FTS
    and vector paths; for ranking metrics only the first (best) occurrence
    matters, so later duplicates are dropped.
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in ranked:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def recall_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Fraction of relevant ids found in the top-``k`` results.

    ``recall@k = |relevant ∩ top_k| / |relevant|``.

    Returns ``0.0`` when ``relevant`` is empty (abstention queries have no
    relevant docs to recall; use :func:`abstention_correct` to score those).
    """
    rel = {str(r) for r in relevant}
    if not rel:
        return 0.0
    top_k = set(_as_list(ranked)[: max(0, int(k))])
    return len(rel & top_k) / len(rel)


def hit_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """1.0 if at least one relevant id appears in the top-``k``, else 0.0.

    This is the "did we surface *anything* useful" signal; ``recall@k``
    measures completeness, ``hit@k`` measures presence.
    """
    rel = {str(r) for r in relevant}
    if not rel:
        return 0.0
    top_k = set(_as_list(ranked)[: max(0, int(k))])
    return 1.0 if rel & top_k else 0.0


def reciprocal_rank(ranked: Sequence[str], relevant: Iterable[str]) -> float:
    """Reciprocal rank of the FIRST relevant id (1-indexed). 0.0 if none found.

    ``RR = 1 / rank_of_first_relevant``. The mean across queries is MRR.
    """
    rel = {str(r) for r in relevant}
    if not rel:
        return 0.0
    for index, item in enumerate(_as_list(ranked), start=1):
        if item in rel:
            return 1.0 / index
    return 0.0


def average_precision(ranked: Sequence[str], relevant: Iterable[str]) -> float:
    """Average precision for one query (the per-query term of MAP).

    Precision is computed at each rank where a relevant id is hit, then
    averaged over the number of relevant ids. 0.0 when ``relevant`` is empty.
    """
    rel = {str(r) for r in relevant}
    if not rel:
        return 0.0
    hits = 0
    score = 0.0
    for index, item in enumerate(_as_list(ranked), start=1):
        if item in rel:
            hits += 1
            score += hits / index
    return score / len(rel)


def abstention_correct(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float | None:
    """Score an abstention query: 1.0 if NO id was surfaced in top-``k``.

    Returns ``None`` for non-abstention queries (``relevant`` non-empty) so the
    caller can keep abstention out of the recall aggregate. For an abstention
    query (empty ``relevant``) the system is correct precisely when it returns
    no candidates — i.e. it knows it does not know.
    """
    rel = {str(r) for r in relevant}
    if rel:
        return None
    top_k = _as_list(ranked)[: max(0, int(k))]
    return 1.0 if not top_k else 0.0


def mean(values: Iterable[float]) -> float:
    """Arithmetic mean; 0.0 over an empty sequence (never raises)."""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return 0.0
    if _np is not None:
        return float(_np.mean(vals))
    return sum(vals) / len(vals)


def stddev(values: Iterable[float]) -> float:
    """Population standard deviation; 0.0 over <2 values (never raises)."""
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 2:
        return 0.0
    if _np is not None:
        return float(_np.std(vals))
    avg = sum(vals) / len(vals)
    return (sum((v - avg) ** 2 for v in vals) / len(vals)) ** 0.5


def aggregate(
    per_query: list[dict],
    ks: Sequence[int] = (1, 5, 10),
) -> dict:
    """Aggregate a list of per-query result dicts into run-level metrics.

    Each item in ``per_query`` must contain:
      - ``ranked``:   list[str]  retrieved ids best-first
      - ``relevant``: list[str]  ground-truth relevant ids (empty => abstention)

    Returns a dict with ``recall@k``/``hit@k`` (over answerable queries only),
    ``mrr``/``map`` (answerable), ``abstention_accuracy`` (abstention queries),
    and counts. Recall is averaged over answerable queries so abstention cases
    (which have no relevant docs) do not deflate it.
    """
    answerable = [q for q in per_query if {str(r) for r in q.get("relevant", [])}]
    abstain = [q for q in per_query if not {str(r) for r in q.get("relevant", [])}]

    out: dict = {
        "n_queries": len(per_query),
        "n_answerable": len(answerable),
        "n_abstention": len(abstain),
    }
    for k in ks:
        out[f"recall@{k}"] = round(
            mean(recall_at_k(q["ranked"], q["relevant"], k) for q in answerable), 4
        )
        out[f"hit@{k}"] = round(
            mean(hit_at_k(q["ranked"], q["relevant"], k) for q in answerable), 4
        )
    out["mrr"] = round(mean(reciprocal_rank(q["ranked"], q["relevant"]) for q in answerable), 4)
    out["map"] = round(mean(average_precision(q["ranked"], q["relevant"]) for q in answerable), 4)

    abst_scores = [
        s
        for s in (abstention_correct(q["ranked"], q["relevant"], max(ks)) for q in abstain)
        if s is not None
    ]
    out["abstention_accuracy"] = round(mean(abst_scores), 4) if abst_scores else None
    return out
