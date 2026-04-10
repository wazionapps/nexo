# NEXO Runtime Benchmark Pack

Generated: 2026-04-10T13:18:49.320070+00:00

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

## Latest run — Memory Recall vs Static CLAUDE.md

- Date: 2026-04-08
- Grading: manual_rubric
- Source markdown: `benchmarks/results/memory-recall-vs-static.md`

| Baseline | Score % | Pass | Partial | Fail |
|----------|---------|------|---------|------|
| NEXO full stack | 100.0 | 5 | 0 | 0 |
| Static CLAUDE.md | 40.0 | 0 | 4 | 1 |
| No memory | 0.0 | 0 | 0 | 5 |

## Methodology

- Grade scale: `pass = 1.0`, `partial = 0.5`, `fail = 0.0`.
- Reruns are reproducible because the scenario catalog and run JSON files are checked in.
- This pack complements LoCoMo and the public scorecard by measuring runtime-backed operator workflows.

## Artifacts

- Catalog: `benchmarks/runtime_pack/scenario_catalog.json`
- Latest summary: `benchmarks/runtime_pack/results/latest_summary.json`
- Latest run file: `benchmarks/runtime_pack/results/2026-04-08-memory-recall-vs-static.json`
