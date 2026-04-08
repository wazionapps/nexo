# Memory Recall vs Static `CLAUDE.md`

Date: 2026-04-08

This is the first checked-in micro-benchmark answering a practical product question:

> If you do not want a full cognitive runtime, how far can a static `CLAUDE.md` take you?

## Baselines

| Baseline | Description |
|----------|-------------|
| `nexo_full_stack` | Shared brain, tools, learnings, workflow runtime, guardrails |
| `static_claude_md` | Large manually maintained `CLAUDE.md`, no MCP memory tools |
| `no_memory` | Fresh session, no persistent memory |

## Results

| Scenario | NEXO full stack | Static `CLAUDE.md` | No memory |
|----------|-----------------|--------------------|-----------|
| Decision rationale recall | Pass | Partial | Fail |
| User preference recall | Pass | Partial | Fail |
| Repeat-error avoidance | Pass | Partial | Fail |
| Interrupted-task resume | Pass | Partial | Fail |
| Related-context stitching | Pass | Fail | Fail |

## Notes

- NEXO wins hardest on the scenarios that require tool-backed retrieval plus durable execution state.
- A static `CLAUDE.md` can carry high-level style and some preferences, but it degrades quickly on:
  - rejected-alternative recall
  - file-scoped prevention rules
  - interrupted work that needs explicit checkpoints
  - context that lives across reminders, followups, learnings, and prior operational work
- `no_memory` fails every scenario because the prompts intentionally omit the answer.

## Why this benchmark matters

This benchmark is deliberately smaller than LoCoMo. It exists because users often compare NEXO not against other research systems, but against the simpler habit of stuffing more instructions into one bootstrap file. The answer from this first run is:

`CLAUDE.md` is useful context. It is not a substitute for a shared brain.
