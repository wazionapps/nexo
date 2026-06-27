# Run NEXO Audit Phase

Use this skill when a NEXO audit phase must be run and the bottleneck is starting a batch of items with empirical discipline.

## Steps
1. Open `goal + workflow + task` and establish the real surface before interpreting the report:
   - active repo/runtime
   - real DB
   - update mechanism
   - tests and git status
2. Set the autonomy rule before starting:
   - Francisco does not want one-by-one checkpoints for mechanical work
   - NEXO creates branches, PRs, merges, and reports afterward with evidence
   - only a huge architectural blast radius deserves a checkpoint
3. Treat proposed code changes as normal reviewed implementation items:
   - use branches, tests, review evidence, and change logs
   - do not rely on removed background apply machinery
   - Deep Sleep code-change findings become internal followups, not executable queues
4. Launch empirical verification of all items in parallel:
   - `grep + read` over code
   - real SQL/schema
   - AST/tests/imports/logs when applicable
   - assume FP until evidence contradicts it
5. Classify each item:
   - `real_gap`
   - `near_fp`
   - `fp`
6. Order only `real_gap` items by risk/blast radius and execute them with an isolated worktree if they touch core.
7. For each `real_gap`:
   - `guard_check`
   - `track`
   - dedicated branch
   - minimal implementation
   - adjacent tests
   - PR + auto-merge squash
   - continue to the next item without waiting for CI unless there is a real blocker
8. For `fp` or `near_fp`, capture a learning/reusable pattern instead of reimplementing.
9. Close the phase with real evidence: PRs, tests, merge status, and verification results.

## Gotchas
- Learning #198: do not confuse "how NEXO works" with per-item permission. Full autonomy still means evidence-backed implementation, not background code application.
- `apply_findings.py` stores Deep Sleep `code_change` findings as internal followups. Implement them through the normal code workflow with tests.
- In Phase 1+2 the automated audit overestimated about 70% of gaps. If there is no hard evidence, do not open code.
