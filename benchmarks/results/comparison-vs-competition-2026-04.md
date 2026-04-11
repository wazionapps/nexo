# NEXO Brain vs Memory Frameworks (April 2026)

> **Closes Fase 5 item 3 of NEXO-AUDIT-2026-04-11.**
>
> The audit asked for a *honest* comparison vs Letta, Mem0, Zep / Graphiti,
> Cognee, and DSPy. This file is the result. We compare on **verifiable
> features and persistence models**, not on benchmark scores we cannot
> reproduce across all 6 systems. For numeric scores against a static
> CLAUDE.md baseline see [`memory-recall-vs-static.md`](./memory-recall-vs-static.md).
>
> If you find anything inaccurate, open an issue at
> https://github.com/wazionapps/nexo/issues — we update this file in place.

## TL;DR

NEXO Brain is the only framework that combines an **MCP-native server**,
a **bi-temporal knowledge graph**, **STM↔LTM Atkinson-Shiffrin memory**,
**adaptive personality modes**, **trust scoring**, **somatic markers**,
**guard-rule discipline**, **outcome tracking**, **cortex evaluation**,
and a **shared brain** across Claude Code / Codex / Claude Desktop —
all under AGPL-3.0, no vendor lock-in.

The trade-off is implementation maturity. Letta and Mem0 are more
polished as products. Zep ships with bigger first-party integrations.
NEXO is closer to a research-grade memory operating system that you run
locally on your machine, not a hosted SaaS. Pick NEXO when you want
**ownership of the cognitive substrate**, full audit log, and you are
comfortable running a local stack.

## Comparison matrix

| Capability | NEXO Brain | Letta | Mem0 | Zep / Graphiti | Cognee | DSPy |
|---|---|---|---|---|---|---|
| **License** | AGPL-3.0 | Apache 2.0 | Apache 2.0 | Apache 2.0 (Zep), Apache 2.0 (Graphiti) | Apache 2.0 | MIT |
| **Hosted SaaS** | No (local) | Yes | Yes | Yes (Zep Cloud) | No | No |
| **MCP-native server** | ✅ FastMCP, 234+ tools | ⚠️ Letta server, MCP via bridge | ❌ REST/SDK | ❌ REST/SDK | ❌ Python SDK | ❌ Python framework |
| **Knowledge graph** | ✅ SQLite KG, **bi-temporal** (valid_from/valid_until + as_of queries + JSON-LD/GraphML export) | ❌ | ❌ | ✅ Graphiti (temporal-aware) | ✅ KG synthesis from text | ❌ |
| **STM↔LTM cognitive memory** | ✅ Atkinson-Shiffrin, Ebbinghaus decay, rehearsal-based promotion, dormant reactivation, dream cycles | ⚠️ Core/archival memory split | ⚠️ Memory ranking | ⚠️ Episodic + semantic | ⚠️ Memory layer | ❌ |
| **Vector + BM25 hybrid search** | ✅ HNSW + BM25 + RRF + temporal boost + KG boost + somatic boost + spreading activation | ✅ Vector | ✅ Vector | ✅ Vector | ✅ Vector | ⚠️ Optional |
| **Sentiment / trust scoring** | ✅ trust_score 0-100 + adaptive_log + dissonance detection + correction fatigue | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Adaptive personality modes** | ✅ FLOW / NORMAL / TENSION with shadow→active learned-weights pipeline | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Somatic markers** | ✅ Per-target risk_score with retrieval reranking | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Cortex / decision evaluation** | ✅ Pre-action gate + post-decision linked outcome tracking + override audit | ❌ | ❌ | ❌ | ❌ | ✅ Optimizer (different scope) |
| **Outcome tracking** | ✅ Auto-promote successful patterns to skills | ❌ | ❌ | ❌ | ❌ | ✅ Bootstrapped few-shot |
| **Protocol debt + guard discipline** | ✅ task_open / task_close gates + guard_check + protocol_debt surfacing in heartbeat | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Self-improvement loop** | ✅ Weekly Evolution cycle + Deep Sleep code_change action + retroactive learning application | ⚠️ Memory consolidation | ❌ | ❌ | ⚠️ KG re-synthesis | ❌ |
| **Hook lifecycle observability** | ✅ hook_runs table + bandit/ruff CI + release contracts | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Multi-client shared brain** | ✅ Claude Code + Codex + Claude Desktop | ❌ | ⚠️ Cross-app via API | ⚠️ Cross-app via API | ❌ | ❌ |
| **Bi-temporal knowledge graph** | ✅ valid_from + valid_until + query_at + JSON-LD/GraphML export | ❌ | ❌ | ✅ Graphiti | ❌ | ❌ |
| **Persistence** | SQLite (local) + cognitive.db | Postgres or SQLite | Vector store + Postgres | Postgres + Neo4j | Postgres + Neo4j + vector store | Stateless |
| **Approx. install size** | ~80 KLOC src + tests, deps: numpy, fastmcp, sqlite-vec | ~70 KLOC, deps: pydantic, sqlmodel, openai | ~30 KLOC, deps: openai, qdrant or vector store | ~50 KLOC, deps: postgres, neo4j (Graphiti) | ~40 KLOC, deps: postgres, neo4j, openai | ~50 KLOC, deps: openai, dspy core |

> Sources: official READMEs and project pages as of April 2026. The
> assertion "no other framework simultaneously implements X, Y, and Z" is
> based on a feature-by-feature scan of the upstream repos on the same
> day; PRs to add a missing capability would update this matrix.

## Where the competition is stronger than NEXO

- **Letta**: cleaner agent framework + tooling around persona/character
  state. If your goal is "build a chat agent with memory", Letta is
  closer to product quality.
- **Mem0**: simpler memory ranking API, easier to glue onto an existing
  OpenAI / Anthropic stack. Lower onboarding cost.
- **Zep / Graphiti**: production-grade Postgres + Neo4j stack with
  battle-tested ingestion pipelines. If you need horizontal scale on
  shared infrastructure, Zep wins.
- **Cognee**: stronger out-of-the-box knowledge graph synthesis from
  unstructured text. NEXO's KG is built incrementally from edits and
  decisions, not from "ingest a folder of PDFs".
- **DSPy**: not a memory system at all — it is a *prompt programming*
  framework with optimizer. Comparing it to NEXO is mostly an "and"
  question: you can use DSPy on top of NEXO's memory.

## Where NEXO is uniquely strong

- **Owns the substrate**: AGPL-3.0, runs locally, no vendor lock-in,
  no API key required for the memory layer.
- **Cognitive vocabulary nobody else even attempts**: somatic markers,
  dissonance, quarantine, protocol debt, adaptive mode, sentiment-driven
  ranking, trust events, outcome → skill promotion, retroactive learning
  application, hook lifecycle observability.
- **Bi-temporal KG with native export**: every relation knows when it
  became true and when (if ever) it stopped being true. The exporter
  emits JSON-LD or GraphML at any past instant via `as_of=` (closes
  Fase 5 item 1).
- **Closed self-improvement loop**: deep sleep → evolution apply
  (sandbox + snapshot + rollback) → cortex validate → outcome→skill
  auto-promote → adaptive rollback → heartbeat surface protocol debt →
  retroactive learning application. The audit identified this loop as
  "teatro" pre-Fase 2; the loop is now closed end-to-end.
- **Multi-client shared brain**: a single nexo.db backs Claude Code,
  Codex, and Claude Desktop simultaneously. Trust events from one
  terminal influence guard checks in another.

## When to pick NEXO Brain

✅ You want to **own** your agent's memory, not rent it.

✅ You are running Claude Code or Codex locally and want them to share
   one cognitive substrate.

✅ You care about **invariants over time** — bi-temporal queries, audit
   trails, decision rationale recall.

✅ You are building agents that need **discipline** (guard checks,
   protocol debt, outcome tracking) more than you need raw conversational
   personality.

## When to pick something else

❌ You want a hosted SaaS with no local install. Use Letta Cloud or
   Zep Cloud.

❌ You need horizontal scale on Postgres + Neo4j out of the box. Use
   Zep / Graphiti.

❌ You want the smallest possible memory ranking layer over OpenAI
   without buying into a cognitive framework. Use Mem0.

❌ You are building a *prompt program*, not a memory system. Use DSPy
   on top of whatever memory layer you already have.

## How to reproduce this comparison locally

```bash
git clone https://github.com/wazionapps/nexo.git
cd nexo
python3 -m pip install -r requirements-dev.txt
python3 -m pytest tests/ -q                # 600+ tests pass
python3 -m ruff check src/                 # 0 errors
python3 -m bandit -r src/ --severity-level high --confidence-level high  # 0 findings
python3 scripts/verify_release_readiness.py --ci  # release contracts pass
```

For the memory recall benchmark vs static CLAUDE.md, see
[`memory-recall-vs-static.md`](./memory-recall-vs-static.md). For the
operator runtime matrix, see
[`operator-runtime-matrix-v5-foundations.md`](./operator-runtime-matrix-v5-foundations.md).

---

*Last updated: 2026-04-11. Maintained by [@wazionapps](https://github.com/wazionapps).*
*Inaccuracies? Open a PR at `benchmarks/results/comparison-vs-competition-2026-04.md`.*
