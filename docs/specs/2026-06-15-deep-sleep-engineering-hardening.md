# Deep Sleep Engineering Hardening - 2026-06-15

## Source Evidence

- Followups `NF-DS-68DEB75F` and `NF-DS-4673D3AB` are absent from the live followups table on 2026-06-14T23:53Z, so the immediate dedupe target is obsolete rather than actionable.
- `src/scripts/deep-sleep/apply_findings.py` already routes learning candidates through `learning_resolver.resolve_learning_candidate(...)` before insert/update.
- `src/enforcement_engine.py` already implements the R14 correction window, and migration `m56_session_correction_requirements` persists correction requirements.
- `workflow_runs.idempotency_key`, email event checkpoints, and followup semantic matching already exist, but they are not yet unified as a run-lock contract for all headless units.

## Decisions

1. Treat the two obsolete followup IDs as absorbed. Do not recreate them.
2. Add a scripted Deep Sleep pre-pass before `synthesize.py` when the next patch is implemented. The pre-pass should emit a compact `learning-consolidation-prepass.json` with duplicate clusters, contradictory clusters, and stale/noise candidates so `synthesize.py` does not push that full reasoning into the model prompt.
3. Keep R14 as the canonical correction-learning hook. The next patch should test the headless false-positive path so operational `context_hint` text from cron runners does not open correction debt.
4. Implement one shared run-lock/idempotency helper for `deep-sleep`, `email_id`, and `followup-runner-cycle`, backed by the existing DB rather than a parallel lock system.
5. Before creating a new guard, queue, supervisor, or recovery layer, the helper must check active learnings and known source modules first.

## Patch Plan

### A. Deep Sleep Learning Pre-Pass

- New module: `src/scripts/deep-sleep/prepass.py`.
- Inputs: active learnings, current extracted findings, existing followups, and last 7 days of applied Deep Sleep actions.
- Output: `runtime/operations/deep-sleep/<date>-learning-consolidation-prepass.json`.
- Required fields: `duplicate_learning_clusters`, `contradiction_candidates`, `stale_followup_ids`, `active_guard_hits`, `prompt_budget_summary`.
- `synthesize.py` should read this file and include only the compact summary in the prompt.

### B. Correction Capture Guard

- Extend tests around `session_correction_requirements` and `enforcement_engine` to assert that a real operator correction opens a requirement and a headless operational heartbeat does not.
- Keep the canonical close path: `nexo_learning_add` resolves the requirement; no second correction table is needed.

### C. Shared Run Lock

- Add a small helper under `src/automation_lock.py` or equivalent existing runtime module.
- Unit key format:
  - `deep-sleep:<date>:<phase>`
  - `email:<email_id>:<stage>`
  - `followup-runner:<cycle_date>:<followup_id>`
- Backing store should reuse existing durable DB patterns and idempotency keys. Do not create a separate JSON lock forest unless DB is unavailable.
- Stale lock policy must be explicit per unit type and tested.

### D. No Parallel Systems Check

- Pre-pass and run-lock helper must query active learnings before proposing new product systems.
- If an existing module covers the capability, the output should reference it and suppress the new-system action.

## Verification Gates

- `pytest tests/test_deep_sleep_synthesize.py tests/test_deep_sleep_apply.py tests/test_correction_requirements.py tests/test_workflow.py`
- A dry run with a synthetic extraction set containing duplicated learning candidates must produce one consolidated pre-pass cluster.
- The next real Deep Sleep run must not fail with timeout `error=124`; if it fails, the failure must identify the next blocking phase rather than re-report the same undifferentiated timeout.

## Non-Goals

- No edits to `~/.nexo/core` from headless daemon context.
- No recreation of obsolete followups.
- No new queue/supervisor layer until existing workflow and memory primitives are exhausted.
