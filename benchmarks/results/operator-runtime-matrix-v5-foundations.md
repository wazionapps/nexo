# Operator Runtime Matrix v5 Foundations

Date: 2026-04-10

This checked-in run expands the first five-scenario micro-benchmark into a broader operator matrix for the areas v5.0 cares about most:

- contradiction handling
- temporal reasoning
- structured recall
- multi-session and cross-client continuity
- outcome-loop usage
- prioritization quality

## Baselines

| Baseline | Description |
|----------|-------------|
| `nexo_full_stack` | Shared brain, tools, durable workflows, outcomes, impact scoring, guardrails, cross-client parity |
| `static_claude_md` | Large manually maintained `CLAUDE.md`, no MCP memory tools or outcome persistence |
| `no_memory` | Fresh session, no persistent memory or stored workflow state |

## Results

| Scenario | NEXO full stack | Static `CLAUDE.md` | No memory |
|----------|-----------------|--------------------|-----------|
| Decision rationale recall | Pass | Partial | Fail |
| User preference recall | Pass | Partial | Fail |
| Repeat-error avoidance | Pass | Partial | Fail |
| Interrupted-task resume | Pass | Partial | Fail |
| Related-context stitching | Pass | Fail | Fail |
| Contradiction latest-wins | Pass | Partial | Fail |
| Temporal reasoning recall | Pass | Partial | Fail |
| Structured domain recall | Pass | Partial | Fail |
| Adversarial / noise rejection | Pass | Partial | Fail |
| Multi-session continuity | Pass | Partial | Fail |
| Cross-client continuity | Pass | Partial | Fail |
| Outcome-loop advantage | Pass | Fail | Fail |
| Prioritization quality | Partial | Partial | Fail |

## Notes

- This run is intentionally conservative on the new v5.0 surfaces:
  - `outcome_loop_advantage` is now `pass` for NEXO because persisted outcome history and captured `outcome-pattern` learnings both change the ranking path, and the regression is checked in.
  - `prioritization_quality` remains `partial` because impact scoring is real and persisted, but long-horizon comparative quality still needs more field data.
- The point of this matrix is not to claim perfection. It is to show that the runtime already dominates on continuity-heavy and retrieval-backed operator work, while being explicit about where the next evidence has to accumulate.

## Why this run matters

The first run answered whether a static bootstrap file can replace a shared brain. This second run answers a harder product question:

> Does the full runtime already produce a broader operator advantage than memory-only recall?

The answer is yes, but not uniformly. The strongest lead is on:

- contradiction and freshness handling
- temporal/contextual continuity
- structured recall and cross-client state

The still-maturing areas are exactly the ones v5.0 is supposed to harden further:

- prioritization quality over time
