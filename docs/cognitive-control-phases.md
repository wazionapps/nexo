# Cognitive Quality Control Phases

This branch implements the five agreed safeguards without merging them into
`main`.

## Phase 0: Read-only observatory

`nexo cognitive-control observatory --json` and MCP
`nexo_cognitive_control_observatory` report Local Context usage, learning
health, followup lifecycle counts and intraday memory health without creating
tasks, injecting context or processing queues.

## Phase 1: Local Context on demand

Pre-answer Local Context supports `off`, `shadow` and `inject` modes through
`NEXO_PRE_ANSWER_LOCAL_CONTEXT_MODE` / `NEXO_LOCAL_CONTEXT_PRE_ANSWER_MODE`.
The router records per-source/per-stage telemetry and uses query/path/entity
prefiltering before the recent-chunk fallback.

## Phase 2: Canonical learning resolver

`learning_resolver.resolve_learning_candidate()` returns exactly one action:
`new`, `merge`, `supersede`, `conflict_review` or `reject`. MCP, Deep Sleep,
the learning validator and daily self-audit call this resolver before writing
or judging learnings.

Authority order is:

1. `francisco_correction`
2. `explicit_instruction`
3. `code_test_evidence`
4. `deep_sleep`
5. `inference`

## Phase 3: Followup lifecycle controller

Followups are classified through `db.followup_lifecycle_lane()` and
`db.followup_lifecycle_snapshot()`. Runner, dashboard and startup context now
share the same lanes: `active`, `waiting_user`, `waiting_external`, `blocked`,
`parked`, `stale_review`, `expired` and `completed`.

## Phase 4: Intraday memory facts

`nexo memory-observations intraday --json` and MCP `nexo_intraday_memory_cycle`
run a low-limit daytime processor. It only publishes temporary
`intraday_fact` hot context for evidence-backed task results, decisions,
corrections and verified code changes. Long-term promotion remains a Deep
Sleep responsibility.
