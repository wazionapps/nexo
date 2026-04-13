# Run Release Final Audit

Use this before claiming a release/publication is closed when you need a live check that smoke, release contract, public surfaces, runtime update/doctor, and protocol closeout still align.

## Steps
1. Run the skill with defaults to audit the current package version. It auto-resolves `release-contracts/v{version}.json` and `scripts/run_vX_Y_smoke.py` when present.
2. Keep `contract="auto"` and `require_contract_complete=true` for the real final gate. Set `contract="none"` only for pre-contract repo checks.
3. For the real post-publish closeout, set `final_closeout=true` and pass the release `protocol_task_id` so the audit also verifies GitHub Release, npm, `nexo update`, runtime doctor, `change_log`, and `task_close`.
4. Treat `ci=true` as repo-only. The last live audit must stay outside CI because `final_closeout` expects a live runtime and a real closed task.

## Gotchas
- A missing auto-resolved contract is a real blocker for the final release audit.
- Smoke is version-line scoped. If no runner exists, the skill reports the skip explicitly instead of pretending it ran.
- The script is read-only except for the optional official `nexo update` step during `final_closeout`; it still does not bump versions, tag, publish, or edit website worktrees.
- `final_closeout` is intentionally stricter than the repo-only readiness pass: it fails if the release task was not closed with evidence or if its `change_log` row is missing.
- If the touched area includes bootstrap, startup, or public claims, finish with the manual watchpoints in `docs/client-parity-checklist.md`.
