# Memory Benchmark Harness

This directory contains the public benchmark material for a simple but important question:

> How much better is NEXO than a static `CLAUDE.md`-only setup on real recall-heavy workflows?

## Scope

This is intentionally a small, reproducible operator benchmark, not a grand universal intelligence claim.

Baselines:

1. `nexo_full_stack`
2. `static_claude_md`
3. `no_memory`

Primary outcomes:

- decision recall
- preference recall
- repeat-error avoidance
- interrupted-task resume
- related-context stitching

## Repro protocol

1. Use one fixed model per run.
2. Keep prompts identical across baselines.
3. Use the same synthetic project history and scenario rubric.
4. Score each scenario as `pass`, `partial`, or `fail`.
5. Record the number of tool calls needed before the correct answer/action.

## Directory layout

- `scenarios/` contains the scenario definitions and expected outputs
- `results/` contains checked-in benchmark runs
- `locomo/` contains the larger checked-in LoCoMo benchmark harness

## First checked-in run

The first micro-benchmark is here:

- [results/memory-recall-vs-static.md](./results/memory-recall-vs-static.md)

This initial run is deliberately modest: five workflow scenarios, manual grading rubric, and a baseline comparison that answers a product question users actually ask.
