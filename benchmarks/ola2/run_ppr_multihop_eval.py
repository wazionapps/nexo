#!/usr/bin/env python3
"""Ola 2 micro-eval — multi-hop recall: PPR (associative_graph) vs 1-hop kg_neighbors.

HONEST SCOPE: LoCoMo/ola1 measure the FTS+vector retrieval path and do NOT
exercise the KG, so they cannot show the PPR lift (see plan section 5.3). This
harness measures the thing the adapter actually changes: given a query seeded on
a file/entity, can we recall a memory that sits 2-3 hops away in the KG?

It seeds an ISOLATED cognitive DB under /tmp (never ~/.nexo), plants N chains of
the form  file -> learning -> decision -> change  (the gold target is the node
2-3 hops from the seed), then scores recall@K for:
  * baseline  = 1-hop kg_neighbors fan-out (what shipped in Ola 1)
  * associative_graph = PPR multi-hop (this work)

Deterministic. Two runs => same numbers.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="ppr_eval_")
os.environ["NEXO_COGNITIVE_DB"] = os.path.join(_TMP, "cognitive.db")
os.environ["NEXO_TEST_DB"] = os.path.join(_TMP, "nexo.db")
os.environ.setdefault("NEXO_SKIP_COGNITIVE_MODEL_DOWNLOAD", "1")
os.environ.setdefault("NEXO_SKIP_FS_INDEX", "1")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import knowledge_graph as kg  # noqa: E402
import causal_graph  # noqa: E402
import ppr  # noqa: E402

N_CHAINS = 25
TOP_K = 12


def seed() -> list[dict]:
    """Plant N chains + a hub all chains hang off (to make it adversarial)."""
    causal_graph.ensure_kg_indexes()
    gold = []
    for i in range(N_CHAINS):
        f = f"file:src/mod_{i}.py"
        l = f"learning:L{i}"
        d = f"decision:D{i}"
        c = f"change:C{i}"
        kg.upsert_edge("file", f, "touched", "learning", l)
        kg.upsert_edge("learning", l, "belongs_to", "decision", d)     # 2 hops
        kg.upsert_edge("decision", d, "ops:produced", "change", c)     # 3 hops
        # every chain also attaches to a shared hub area (the trap)
        kg.upsert_edge("learning", l, "belongs_to_area", "area", "area:general")
        gold.append({"seed_file": f, "gold_2hop": d, "gold_3hop": c})
    return gold


def node_id(ntype, nref):
    n = kg.get_node(ntype, nref)
    return int(n["id"]) if n else None


def one_hop_ids(seed_id: int) -> set[int]:
    out = set()
    for nb in kg.get_neighbors(seed_id, active_only=True):
        nid = int(nb["target_id"]) if int(nb["source_id"]) == seed_id else int(nb["source_id"])
        out.add(nid)
    return out


def main() -> None:
    gold = seed()
    g = ppr.build_graph()

    base_hit2 = base_hit3 = ppr_hit2 = ppr_hit3 = 0
    for item in gold:
        sid = node_id("file", item["seed_file"])
        g2 = node_id("decision", item["gold_2hop"])
        g3 = node_id("change", item["gold_3hop"])

        # baseline: 1-hop fan-out
        oneh = one_hop_ids(sid)
        base_hit2 += int(g2 in oneh)
        base_hit3 += int(g3 in oneh)

        # associative_graph: PPR top-K
        ranked = {r.node_id for r in ppr.rank_related({sid: 1.0}, graph=g, top_n=TOP_K)}
        ppr_hit2 += int(g2 in ranked)
        ppr_hit3 += int(g3 in ranked)

    n = len(gold)
    report = {
        "suite": "ola2_ppr_multihop",
        "n_chains": n,
        "top_k": TOP_K,
        "baseline_kg_neighbors_1hop": {
            "recall@k_2hop_target": round(base_hit2 / n, 4),
            "recall@k_3hop_target": round(base_hit3 / n, 4),
        },
        "associative_graph_ppr": {
            "recall@k_2hop_target": round(ppr_hit2 / n, 4),
            "recall@k_3hop_target": round(ppr_hit3 / n, 4),
        },
        "lift": {
            "2hop": round((ppr_hit2 - base_hit2) / n, 4),
            "3hop": round((ppr_hit3 - base_hit3) / n, 4),
        },
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
