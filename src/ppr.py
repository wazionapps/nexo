"""Personalized PageRank (PPR) over the NEXO Knowledge Graph — Ola 2.

"Connect the dots at answer time" (HippoRAG2 style). The answer-path already
fans out 1-hop from query entities/files via ``kg_neighbors``; this module
generalises that to a multi-hop, *ranked* spreading-activation that pulls in
nodes 2-3 hops away that a 1-hop fan-out never reaches.

Design decisions (approved):

* **Pure-Python forward-push** (Andersen-Chung-Lang local push), no numpy/scipy
  in the hot-path. Localised: touches only the few hundred nodes that accumulate
  mass above ``eps`` for a focused query -> sub-30ms on the real KG.
* **Substrate = the existing ``kg_nodes`` / ``kg_edges`` (cognitive.db)**. No new
  graph. One bulk load of active edges (``valid_until IS NULL``, ~13k rows) per
  build; never ``get_neighbors`` in a loop.
* **Column-stochastic transition** ``w_uv = (weight*confidence) / Sigma_out`` is
  MANDATORY: the KG is hub-dominated (e.g. ``area:general`` out-degree ~893). A
  naive PPR would dump all mass on those hubs and always return them. Normalising
  by the per-node outgoing mass neutralises that. Relation weights additionally
  down-weight the noisy structural hubs (``belongs_to_area`` / ``describes_session``).
* **Fail-open absolute**: every public entrypoint is bounded (``max_push``) and
  wrapped so a large/slow/broken graph degrades to a 1-hop neighbour list or an
  empty result, and NEVER raises into the answer-path.

The module is import-light and side-effect free at import time. It only reads the
KG; it never writes.
"""

from __future__ import annotations

import heapq
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable


# --- Tunables (all bounded; see plan section 4.1) ----------------------------

DEFAULT_ALPHA = 0.15          # teleport / restart probability
DEFAULT_EPS = 1e-4            # residual mass threshold for the push frontier
DEFAULT_MAX_PUSH = 2000       # hard cap on push operations (bounds latency)
DEFAULT_TOP_N = 12            # max ranked nodes returned
DEFAULT_MAX_SEEDS = 8         # cap on personalization seeds
# Fraction of an edge's mass that flows along the *reverse* direction. KG
# relations are asymmetric (ops:produced != causal:motivated_by), so we follow
# outgoing edges primarily but let a small share traverse backwards so a query
# seeded on a leaf (e.g. a learning) can still reach its parents.
DEFAULT_REVERSE_FRACTION = 0.35

# Per-relation multipliers applied BEFORE column normalisation. The big
# structural relations (area / session membership) are the hubs; damping them
# keeps the walk on the semantically informative edges (causal / provenance /
# file-application). Unknown relations default to 1.0.
RELATION_WEIGHTS: dict[str, float] = {
    "causal:verified_by": 1.6,
    "causal:resolved_by": 1.6,
    "causal:motivated_by": 1.4,
    "ops:produced": 1.3,
    "applies_to_file": 1.1,
    "touched": 1.1,
    "belongs_to": 1.0,
    "mentions_email": 0.9,
    "in_domain": 0.8,
    "belongs_to_area": 0.35,
    "describes_session": 0.30,
}


@dataclass
class PPRGraph:
    """In-memory adjacency built from one bulk load of active KG edges.

    ``out[u]`` and ``inc[u]`` map a node id to a list of ``(neighbour_id, mass)``
    where ``mass`` is the relation-weighted ``weight*confidence`` of the edge
    (pre-normalisation). ``node_meta`` carries the resolved type/ref/label so the
    adapter never has to round-trip back to SQLite per result node.
    """

    out: dict[int, list[tuple[int, float]]] = field(default_factory=dict)
    inc: dict[int, list[tuple[int, float]]] = field(default_factory=dict)
    out_sum: dict[int, float] = field(default_factory=dict)
    node_meta: dict[int, dict[str, Any]] = field(default_factory=dict)
    edge_count: int = 0

    def degree_out(self, node_id: int) -> int:
        return len(self.out.get(node_id, ()))

    def transitions(self, node_id: int, *, reverse_fraction: float) -> Iterable[tuple[int, float]]:
        """Yield (neighbour, column-stochastic transition prob) for a node.

        Combines outgoing edges (weight 1) and incoming edges (weight
        ``reverse_fraction``), then normalises the combined mass to sum to 1 so
        the operator is column-stochastic and hub-safe.
        """
        out_edges = self.out.get(node_id, ())
        inc_edges = self.inc.get(node_id, ())
        if not out_edges and not inc_edges:
            return ()
        combined: dict[int, float] = {}
        for v, m in out_edges:
            combined[v] = combined.get(v, 0.0) + m
        if reverse_fraction > 0:
            for v, m in inc_edges:
                combined[v] = combined.get(v, 0.0) + m * reverse_fraction
        total = sum(combined.values())
        if total <= 0:
            return ()
        return [(v, m / total) for v, m in combined.items()]


def _relation_weight(relation: str) -> float:
    return RELATION_WEIGHTS.get(str(relation or ""), 1.0)


def build_graph(*, max_edges: int | None = None) -> PPRGraph:
    """Bulk-load active KG edges into an in-memory adjacency.

    Single ``SELECT`` over ``kg_edges WHERE valid_until IS NULL`` (~13k rows).
    Node metadata is loaded in one further ``SELECT`` over ``kg_nodes``.
    Raises only on a genuinely broken DB; callers wrap this.
    """
    import knowledge_graph as kg

    db = kg._get_db()
    graph = PPRGraph()
    sql = (
        "SELECT source_id, target_id, relation, weight, confidence "
        "FROM kg_edges WHERE valid_until IS NULL"
    )
    if max_edges and max_edges > 0:
        sql += f" LIMIT {int(max_edges)}"
    rows = db.execute(sql).fetchall()
    for row in rows:
        src = int(row["source_id"])
        tgt = int(row["target_id"])
        if src == tgt:
            continue  # self-loop carries no associative signal
        w = float(row["weight"] if row["weight"] is not None else 1.0)
        c = float(row["confidence"] if row["confidence"] is not None else 1.0)
        mass = max(0.0, w) * max(0.0, c) * _relation_weight(row["relation"])
        if mass <= 0:
            continue
        graph.out.setdefault(src, []).append((tgt, mass))
        graph.inc.setdefault(tgt, []).append((src, mass))
        graph.out_sum[src] = graph.out_sum.get(src, 0.0) + mass
        graph.edge_count += 1

    # Resolve node metadata in one shot (only for nodes that appear in an edge).
    touched = set(graph.out) | set(graph.inc)
    if touched:
        node_rows = db.execute(
            "SELECT id, node_type, node_ref, label FROM kg_nodes"
        ).fetchall()
        for nr in node_rows:
            nid = int(nr["id"])
            if nid in touched:
                graph.node_meta[nid] = {
                    "id": nid,
                    "node_type": nr["node_type"],
                    "node_ref": nr["node_ref"],
                    "label": nr["label"],
                }
    return graph


# --- Per-process graph cache + safe background pre-warm ----------------------
#
# WHY A CACHE. ``build_graph`` is a bulk load of all active KG edges (~13k rows
# on the real KG, ~20ms) plus a node-metadata SELECT. The adapter used to pay
# that on EVERY answer. Worse, a cold process pays it on top of imports +
# entity resolution + the first SQLite touch, and the combined ~167ms blows the
# 120ms per-source step timeout, so the dispatcher aborts the step
# (``aborted_reason='timeout'``) and the feature contributes nothing on query-1.
# Caching the built graph per process drops warm queries to ~5-7ms.
#
# WHY (MAX(id), COUNT) AND NOT ``get_change_watermark``. The working-memory
# resolution cache invalidates on ``db.get_change_watermark`` == ``MAX(id) FROM
# change_log``. But the KG write path (``knowledge_graph.upsert_edge`` /
# ``delete_edge``) mutates ``kg_edges`` DIRECTLY and never appends to
# ``change_log`` — so the global watermark does NOT move when edges are added,
# superseded or retired, and reusing it would serve a stale graph forever. The
# correct, equally-cheap KG-local fingerprint is a single SELECT of
# ``(MAX(id), COUNT(*))`` over active edges (``valid_until IS NULL``). It catches
# every mutation shape coherently with the anti-stale discipline:
#   * ADD          -> a new row: MAX(id) rises (and COUNT rises)
#   * UPDATE        -> supersede: old row gets valid_until + new row inserted ->
#                      MAX(id) rises
#   * DELETE        -> supersede: old row gets valid_until -> active COUNT drops
# So whenever the KG changes, the fingerprint changes and the cache rebuilds.
#
# The cache is keyed by ``(db_path, fingerprint)`` so a test that swaps
# ``COGNITIVE_DB`` to a tmp file (conftest isolation) never reads another DB's
# graph, and a moved/rotated prod DB rebuilds rather than serving the old one.

_GRAPH_CACHE: dict[str, tuple[tuple[int, int], "PPRGraph"]] = {}
_GRAPH_CACHE_LOCK = threading.Lock()
_PREWARM_LOCK = threading.Lock()
_prewarm_thread: threading.Thread | None = None

# Cache statistics (process-local; used by tests/benchmarks to assert that a
# warm call did NOT rebuild). Best-effort, not thread-perfect.
_CACHE_STATS = {"builds": 0, "hits": 0}


def _kg_db_path() -> str:
    """Resolved cognitive.db path — the cache namespace. Cheap, no connection."""
    try:
        import cognitive._core as cog_core

        return str(cog_core.COGNITIVE_DB)
    except Exception:
        return ""


def kg_fingerprint() -> tuple[int, int]:
    """One-SELECT KG-local invalidation signal: (MAX active edge id, active count).

    Cheap (indexed AUTOINCREMENT MAX + COUNT), monotonic-enough to detect every
    add/supersede/retire on ``kg_edges``. Returns ``(0, 0)`` if the KG is empty
    or unavailable — a fresh cache entry stores that same value, so an empty KG
    never spuriously invalidates. Never raises (callers depend on fail-open).
    """
    try:
        import knowledge_graph as kg

        row = kg._get_db().execute(
            "SELECT MAX(id), COUNT(*) FROM kg_edges WHERE valid_until IS NULL"
        ).fetchone()
    except Exception:
        return (0, 0)
    if not row:
        return (0, 0)
    try:
        max_id = int(row[0]) if row[0] is not None else 0
        count = int(row[1]) if row[1] is not None else 0
    except (TypeError, ValueError):
        return (0, 0)
    return (max_id, count)


def get_cached_graph(*, max_edges: int | None = None) -> PPRGraph:
    """Return the per-process graph, rebuilding only when the KG fingerprint moved.

    Build cost is paid once per process (or once per KG change). Fail-open is
    handled by callers (``rank_related`` / the adapter wrap this); a genuinely
    broken DB still raises here so those wrappers can degrade.
    """
    db_path = _kg_db_path()
    fp = kg_fingerprint()
    cached = _GRAPH_CACHE.get(db_path)
    if cached is not None and cached[0] == fp:
        _CACHE_STATS["hits"] += 1
        return cached[1]
    # Build outside the lock would risk a double-build under contention; the
    # build is bounded and infrequent, so we serialise it. A second waiter that
    # finds the now-current entry returns it without rebuilding.
    with _GRAPH_CACHE_LOCK:
        cached = _GRAPH_CACHE.get(db_path)
        if cached is not None and cached[0] == fp:
            _CACHE_STATS["hits"] += 1
            return cached[1]
        graph = build_graph(max_edges=max_edges)
        _GRAPH_CACHE[db_path] = (fp, graph)
        _CACHE_STATS["builds"] += 1
        return graph


def cache_is_warm() -> bool:
    """True iff a graph for the CURRENT db+fingerprint is already cached.

    Used by the adapter to decide, without building, whether query-1 can get the
    multi-hop graph immediately or must degrade to 1-hop while the pre-warm runs.
    """
    cached = _GRAPH_CACHE.get(_kg_db_path())
    return cached is not None and cached[0] == kg_fingerprint()


def reset_graph_cache() -> None:
    """Drop the cache (and stats). For tests and explicit invalidation."""
    with _GRAPH_CACHE_LOCK:
        _GRAPH_CACHE.clear()
    _CACHE_STATS["builds"] = 0
    _CACHE_STATS["hits"] = 0


def prewarm_async() -> None:
    """Build the graph in a background daemon thread without blocking the caller.

    Cold-start strategy (decision): the FIRST answer of a fresh process must not
    pay the full build under a 120ms step timeout. So instead of building inline,
    the adapter (or router init) calls this to warm the cache off the hot-path.
    If the warm has not finished when query-1 arrives, the adapter degrades
    cleanly to 1-hop ``fallback_neighbors`` (fast, never times out). Query-2+
    then finds a warm cache and gets full multi-hop PPR.

    Lazy + idempotent: at most one warm thread at a time; a no-op if the cache is
    already warm. Fail-open: a build error in the thread is swallowed (the next
    inline ``get_cached_graph`` will retry / degrade). Daemon so it never blocks
    process exit.
    """
    global _prewarm_thread
    if cache_is_warm():
        return
    with _PREWARM_LOCK:
        if _prewarm_thread is not None and _prewarm_thread.is_alive():
            return
        if cache_is_warm():
            return

        def _warm() -> None:
            try:
                get_cached_graph()
            except Exception:
                pass  # fail-open: inline path will retry/degrade

        t = threading.Thread(target=_warm, name="ppr-prewarm", daemon=True)
        _prewarm_thread = t
        t.start()


def push_ppr(
    graph: PPRGraph,
    seeds: dict[int, float],
    *,
    alpha: float = DEFAULT_ALPHA,
    eps: float = DEFAULT_EPS,
    max_push: int = DEFAULT_MAX_PUSH,
    reverse_fraction: float = DEFAULT_REVERSE_FRACTION,
) -> dict[int, float]:
    """Forward-push approximate Personalized PageRank (Andersen-Chung-Lang).

    ``seeds`` is the personalization vector ``{node_id: weight}`` (need not be
    pre-normalised; it is normalised to sum 1 here). Returns the PPR estimate
    ``{node_id: score}`` over the nodes touched by the push. Deterministic for a
    fixed graph + seeds (ties broken by node id in the frontier).
    """
    if not seeds:
        return {}
    total = sum(max(0.0, v) for v in seeds.values())
    if total <= 0:
        return {}

    estimate: dict[int, float] = {}
    residual: dict[int, float] = {n: max(0.0, v) / total for n, v in seeds.items() if v > 0}

    # Max-heap on residual mass (negate for heapq min-heap). The (-, id) tuple
    # makes ordering total and the result deterministic.
    frontier: list[tuple[float, int]] = [(-r, n) for n, r in residual.items()]
    heapq.heapify(frontier)
    in_frontier = set(residual)

    pushes = 0
    while frontier and pushes < max_push:
        neg_r, u = heapq.heappop(frontier)
        in_frontier.discard(u)
        r = residual.get(u, 0.0)
        if r <= 0:
            continue
        deg = graph.degree_out(u) or 1
        # Skip nodes whose residual is below the per-degree threshold (standard
        # ACL stopping condition); they cannot meaningfully change the ranking.
        if r < eps * deg and u not in seeds:
            continue

        estimate[u] = estimate.get(u, 0.0) + alpha * r
        mass = (1.0 - alpha) * r
        residual[u] = 0.0
        pushes += 1

        for v, p_uv in graph.transitions(u, reverse_fraction=reverse_fraction):
            add = mass * p_uv
            if add <= 0:
                continue
            new_r = residual.get(v, 0.0) + add
            residual[v] = new_r
            if v not in in_frontier:
                heapq.heappush(frontier, (-new_r, v))
                in_frontier.add(v)

    return estimate


@dataclass
class RankedNode:
    node_id: int
    score: float
    node_type: str
    node_ref: str
    label: str


def rank_related(
    seeds: dict[int, float],
    *,
    graph: PPRGraph | None = None,
    top_n: int = DEFAULT_TOP_N,
    alpha: float = DEFAULT_ALPHA,
    eps: float = DEFAULT_EPS,
    max_push: int = DEFAULT_MAX_PUSH,
    reverse_fraction: float = DEFAULT_REVERSE_FRACTION,
) -> list[RankedNode]:
    """Run PPR from ``seeds`` and return the top-N related nodes (seeds removed).

    Builds the graph if not supplied. Fail-open: any error returns ``[]``.
    """
    try:
        if not seeds:
            return []
        if graph is None:
            graph = get_cached_graph()
        estimate = push_ppr(
            graph,
            seeds,
            alpha=alpha,
            eps=eps,
            max_push=max_push,
            reverse_fraction=reverse_fraction,
        )
        seed_ids = set(seeds)
        ranked = [
            (nid, score)
            for nid, score in estimate.items()
            if nid not in seed_ids and score > 0
        ]
        # Deterministic order: score desc, then node id asc.
        ranked.sort(key=lambda item: (-item[1], item[0]))
        out: list[RankedNode] = []
        for nid, score in ranked[: max(0, int(top_n))]:
            meta = graph.node_meta.get(nid) or {}
            out.append(
                RankedNode(
                    node_id=nid,
                    score=round(float(score), 6),
                    node_type=str(meta.get("node_type") or ""),
                    node_ref=str(meta.get("node_ref") or ""),
                    label=str(meta.get("label") or ""),
                )
            )
        return out
    except Exception:
        return []


def fallback_neighbors(seed_ids: Iterable[int], *, limit: int = 6) -> list[RankedNode]:
    """1-hop degraded path (parity with ``kg_neighbors``) if PPR can't run.

    Used when the graph is too large/slow or PPR fails. Fail-open: returns ``[]``
    on any error.
    """
    try:
        import knowledge_graph as kg

        seen: set[int] = set()
        out: list[RankedNode] = []
        for sid in seed_ids:
            for nb in kg.get_neighbors(int(sid), active_only=True)[:limit]:
                # neighbour node id is the *other* endpoint
                nid = int(nb["target_id"]) if int(nb["source_id"]) == int(sid) else int(nb["source_id"])
                if nid in seen or nid in set(seed_ids):
                    continue
                seen.add(nid)
                out.append(
                    RankedNode(
                        node_id=nid,
                        score=0.0,
                        node_type=str(nb.get("node_type") or ""),
                        node_ref=str(nb.get("node_ref") or ""),
                        label=str(nb.get("label") or ""),
                    )
                )
        return out[: max(0, limit * 2)]
    except Exception:
        return []
