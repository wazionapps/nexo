"""F0 probe (Ola 2): KG density + PPR microbench + anti-hub / multi-hop demo.

Reads the REAL live KG (cognitive.db). Pure measurement; never writes.
Run: NEXO_COGNITIVE_DB=... python3 scripts/ppr_kg_probe.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import ppr  # noqa: E402
import knowledge_graph as kg  # noqa: E402
import causal_graph  # noqa: E402


def main() -> None:
    causal_graph.ensure_kg_indexes()
    db = kg._get_db()

    n_nodes = db.execute("SELECT COUNT(*) c FROM kg_nodes").fetchone()["c"]
    n_edges = db.execute(
        "SELECT COUNT(*) c FROM kg_edges WHERE valid_until IS NULL"
    ).fetchone()["c"]
    print(f"== KG DENSITY ==  nodes={n_nodes}  active_edges={n_edges}")

    t0 = time.monotonic()
    g = ppr.build_graph()
    build_ms = (time.monotonic() - t0) * 1000.0
    print(f"build_graph: {build_ms:.1f}ms  edges_loaded={g.edge_count}  meta={len(g.node_meta)}")

    # Top hubs by raw out-degree (the anti-hub risk).
    hubs = sorted(g.out.items(), key=lambda kv: len(kv[1]), reverse=True)[:6]
    print("== TOP HUBS (out-degree) ==")
    for nid, edges in hubs:
        meta = g.node_meta.get(nid, {})
        print(f"  {meta.get('node_type')}:{meta.get('node_ref')}  out={len(edges)}")

    # Pick a non-hub seed: a 'file' node with modest degree that is NOT a hub.
    file_seed = db.execute(
        "SELECT id, node_type, node_ref FROM kg_nodes WHERE node_type='file' "
        "AND node_ref LIKE '%pre_answer_router%' LIMIT 1"
    ).fetchone()
    if file_seed is None:
        file_seed = db.execute(
            "SELECT id, node_type, node_ref FROM kg_nodes WHERE node_type='file' LIMIT 1"
        ).fetchone()
    seed_id = int(file_seed["id"])
    print(f"\n== SEED ==  {file_seed['node_type']}:{file_seed['node_ref']} (id={seed_id})")

    # ---- ANTI-HUB demo: with vs without column-stochastic normalisation ----
    # WITH normalisation = the shipped algorithm.
    ranked_norm = ppr.rank_related({seed_id: 1.0}, graph=g, top_n=10)
    general_in_norm = any("general" in r.node_ref or "general" in r.label.lower() for r in ranked_norm)
    print("\n== ANTI-HUB: WITH column-stochastic normalisation (shipped) ==")
    for r in ranked_norm[:8]:
        print(f"  {r.score:.5f}  {r.node_type}:{r.node_ref}")
    print(f"  -> returns area:general? {general_in_norm}")

    # WITHOUT normalisation: a naive PPR that pushes raw edge mass (no per-node
    # normalisation) and ignores relation down-weighting -> demonstrate hub bias.
    naive = _naive_unnormalized_ppr(g, {seed_id: 1.0}, top_n=10)
    general_in_naive = any("general" in nr for _, nr in naive)
    print("\n== ANTI-HUB: WITHOUT normalisation (naive, for contrast) ==")
    for score, nr in naive[:8]:
        print(f"  {score:.5f}  {nr}")
    print(f"  -> returns area:general? {general_in_naive}")

    # ---- MULTI-HOP demo: 1-hop neighbours vs PPR reach ----
    one_hop = {
        int(nb["target_id"]) if int(nb["source_id"]) == seed_id else int(nb["source_id"])
        for nb in kg.get_neighbors(seed_id, active_only=True)
    }
    ppr_ids = {r.node_id for r in ranked_norm}
    multi_hop_only = ppr_ids - one_hop
    print(f"\n== MULTI-HOP ==  1-hop neighbours={len(one_hop)}  ppr_top={len(ppr_ids)}  ppr-only(>=2hop)={len(multi_hop_only)}")
    for r in ranked_norm:
        if r.node_id in multi_hop_only:
            print(f"  [>=2hop] {r.score:.5f}  {r.node_type}:{r.node_ref}")

    # ---- LATENCY microbench: 100 push runs from the seed ----
    times = []
    for _ in range(100):
        t = time.monotonic()
        ppr.push_ppr(g, {seed_id: 1.0})
        times.append((time.monotonic() - t) * 1000.0)
    times.sort()
    print(f"\n== LATENCY (push only, 100 runs) ==  p50={times[50]:.3f}ms  p95={times[95]:.3f}ms  max={times[-1]:.3f}ms")

    # End-to-end (build + rank) once more for a realistic per-call number.
    ranked2, e2e = ppr.timed_rank_related({seed_id: 1.0}, top_n=12)
    print(f"== LATENCY (build+rank end-to-end) ==  {e2e:.1f}ms  results={len(ranked2)}")


def _naive_unnormalized_ppr(g: ppr.PPRGraph, seeds, top_n=10):
    """Contrast algorithm: pushes RAW edge mass with NO per-node normalisation
    and NO relation down-weighting. This is what a hub-naive PPR looks like."""
    import knowledge_graph as kg
    db = kg._get_db()
    # rebuild raw (weight*confidence only) adjacency, no relation weights
    raw_out: dict[int, list[tuple[int, float]]] = {}
    raw_sum: dict[int, float] = {}
    for row in db.execute(
        "SELECT source_id, target_id, weight, confidence FROM kg_edges WHERE valid_until IS NULL"
    ).fetchall():
        s, t = int(row["source_id"]), int(row["target_id"])
        if s == t:
            continue
        m = max(0.0, float(row["weight"] or 1.0)) * max(0.0, float(row["confidence"] or 1.0))
        raw_out.setdefault(s, []).append((t, m))
        raw_sum[s] = raw_sum.get(s, 0.0) + m
    estimate: dict[int, float] = {}
    residual = dict(seeds)
    for _ in range(40):  # plain power-iteration-ish pushes
        nxt: dict[int, float] = {}
        for u, r in residual.items():
            if r <= 0:
                continue
            estimate[u] = estimate.get(u, 0.0) + 0.15 * r
            mass = 0.85 * r
            for v, m in raw_out.get(u, ()):
                # NO normalisation: raw mass flows -> hubs accumulate everything
                nxt[v] = nxt.get(v, 0.0) + mass * m
        residual = nxt
        if not residual:
            break
    seed_ids = set(seeds)
    ranked = sorted(
        ((s, nid) for nid, s in estimate.items() if nid not in seed_ids),
        reverse=True,
    )
    meta = {}
    rows = db.execute("SELECT id, node_type, node_ref FROM kg_nodes").fetchall()
    for nr in rows:
        meta[int(nr["id"])] = f"{nr['node_type']}:{nr['node_ref']}"
    return [(s, meta.get(nid, str(nid))) for s, nid in ranked[:top_n]]


if __name__ == "__main__":
    main()
