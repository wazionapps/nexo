# NEXO Compare Scorecard

NEXO is the local cognitive runtime that makes the model around your model smarter.

## Measured benchmark
- LoCoMo overall F1: 0.5875
- LoCoMo overall recall: 0.7487
- Open-domain F1: 0.6366
- Multi-hop F1: 0.3329
- Temporal F1: 0.3258

## Ablation / baseline suite
- Combined external + NEXO ablation baselines (2026-04-06)
- Raw model baseline (GPT-4 128K full context): F1 0.379
- Gemini Pro 1.0 baseline: F1 0.313
- Retrieval baseline (GPT-3.5 + Contriever RAG): F1 0.283
- NEXO memory-only mode (LoCoMo RAG): F1 0.5875
- NEXO cognitive-cycle mode: F1 0.2931

## Longitudinal local runtime metrics
- 30d: success 100.0% | avg close 0.0 min | recovery n/a | open protocol debt 0 | unnecessary tool 3.7% | cost/solved 0.143791 USD
- 60d: success 100.0% | avg close 0.0 min | recovery n/a | open protocol debt 0 | unnecessary tool 3.7% | cost/solved 0.143791 USD
- 90d: success 100.0% | avg close 0.0 min | recovery n/a | open protocol debt 0 | unnecessary tool 3.7% | cost/solved 0.143791 USD

## System On Top Of Model
![NEXO system on top of model](../assets/nexo-brain-architecture.png)

## Public API surface
- MCP wrappers: `nexo_remember`, `nexo_memory_recall`, `nexo_consolidate`, `nexo_run_workflow`
- Python SDK: `src/nexo_sdk.py`
- Quickstart: `docs/quickstart-5-minutes.md`

## Client parity guardrails
- `scripts/verify_client_parity.py`
- `docs/client-parity-checklist.md`
- runtime doctor parity audits
