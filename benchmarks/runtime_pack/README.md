# NEXO Runtime Benchmark Pack

Generated: 2026-04-10T14:06:55.449677+00:00

## What this is

- Small reproducible operator benchmark focused on runtime-backed recall and continuity, not a universal agent benchmark.
- Deterministic aggregation over checked-in scenario definitions and scored run files.
- A runtime-focused comparison against realistic local baselines, not a universal intelligence claim.

## Scenario catalog

- `decision_rationale_recall` — Decision rationale recall (memory_reasoning)
  - Detail: `benchmarks/scenarios/decision-rationale-recall.md`
- `user_preference_recall` — User preference recall (operator_memory)
  - Detail: `benchmarks/scenarios/user-preference-recall.md`
- `repeat_error_avoidance` — Repeat-error avoidance (guarded_execution)
  - Detail: `benchmarks/scenarios/repeat-error-avoidance.md`
- `interrupted_task_resume` — Interrupted task resume (durable_workflow)
  - Detail: `benchmarks/scenarios/interrupted-task-resume.md`
- `related_context_stitching` — Related-context stitching (cross_thread_continuity)
  - Detail: `benchmarks/scenarios/related-context-stitching.md`
- `contradiction_latest_wins` — Contradiction latest-wins (contradiction_handling)
  - Detail: `benchmarks/scenarios/contradiction-latest-wins.md`
- `temporal_reasoning_recall` — Temporal reasoning recall (temporal_reasoning)
  - Detail: `benchmarks/scenarios/temporal-reasoning-recall.md`
- `structured_domain_recall` — Structured domain recall (structured_recall)
  - Detail: `benchmarks/scenarios/structured-domain-recall.md`
- `adversarial_noise_rejection` — Adversarial / noise rejection (retrieval_hygiene)
  - Detail: `benchmarks/scenarios/adversarial-noise-rejection.md`
- `multi_session_continuity` — Multi-session continuity (multi_session)
  - Detail: `benchmarks/scenarios/multi-session-continuity.md`
- `cross_client_continuity` — Cross-client continuity (client_parity)
  - Detail: `benchmarks/scenarios/cross-client-continuity.md`
- `outcome_loop_advantage` — Outcome-loop advantage (outcome_optimization)
  - Detail: `benchmarks/scenarios/outcome-loop-advantage.md`
- `prioritization_quality` — Prioritization quality (queue_optimization)
  - Detail: `benchmarks/scenarios/prioritization-quality.md`

## Latest run — Operator Runtime Matrix v5 Foundations

- Date: 2026-04-10
- Grading: manual_rubric
- Scenario count: 13
- Source markdown: `benchmarks/results/operator-runtime-matrix-v5-foundations.md`

| Baseline | Score % | Pass | Partial | Fail |
|----------|---------|------|---------|------|
| NEXO full stack | 96.2 | 12 | 1 | 0 |
| Static CLAUDE.md | 42.3 | 0 | 11 | 2 |
| No memory | 0.0 | 0 | 0 | 13 |

### Latest run notes

- Second checked-in runtime-pack run: expands the benchmark matrix to contradiction, temporal, structured recall, multi-session, cross-client, outcome-loop, and prioritization.
- Outcome-loop advantage now reaches pass: persisted outcome history plus captured outcome-pattern learnings change the ranking path with checked-in regression coverage.
- Prioritization quality stays partial: impact scoring is real and persisted, but long-horizon comparative quality still needs more field data.

## Methodology

- Grade scale: `pass = 1.0`, `partial = 0.5`, `fail = 0.0`.
- Reruns are reproducible because the scenario catalog and run JSON files are checked in.
- This pack complements LoCoMo and the public scorecard by measuring runtime-backed operator workflows.

## Artifacts

- Catalog: `benchmarks/runtime_pack/scenario_catalog.json`
- Latest summary: `benchmarks/runtime_pack/results/latest_summary.json`
- Latest run file: `benchmarks/runtime_pack/results/2026-04-10-operator-runtime-matrix-v5-foundations.json`
