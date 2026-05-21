# Run NEXO Audit Phase

Use this skill when a NEXO audit phase must be run and the bottleneck is deciding the `evolution_apply` scope and starting a batch of items with empirical discipline.

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
3. Treat `evolution_apply` as a technical implementation decision, not as human permission:
   - the apply path already exists through `evolution_log` + `_apply_accepted_proposals`
   - sandbox/snapshot/rollback protects materialization of the accepted change
   - do not duplicate that mechanism in deep sleep or the audit runner
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
- Learning #198: do not confuse "how NEXO works" with "what evolution_apply may apply". The first is already settled: full autonomy.
- `apply_findings.py` already stages `code_change` in `evolution_log`; `nexo-evolution-run.py` already consumes `accepted` with sandbox/snapshot/rollback. If the item asks for that, first verify whether it already exists.
- In Phase 1+2 the automated audit overestimated about 70% of gaps. If there is no hard evidence, do not open code.
