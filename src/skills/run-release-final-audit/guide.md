# Run Release Final Audit

Use this before the final release/publication package when you need a live check that smoke, release contract, changelog/version surfaces, client parity, and runtime doctor still align.

## Steps
1. Run the skill with defaults to audit the current package version. It auto-resolves `release-contracts/v{version}.json` and `scripts/run_vX_Y_smoke.py` when present.
2. Keep `contract="auto"` and `require_contract_complete=true` for the real final gate. Set `contract="none"` only for pre-contract repo checks.
3. Treat `ci=true` as repo-only. The last live audit should keep runtime doctor enabled.

## Gotchas
- A missing auto-resolved contract is a real blocker for the final release audit.
- Smoke is version-line scoped. If no runner exists, the skill reports the skip explicitly instead of pretending it ran.
- The script is read-only. It verifies readiness but does not bump versions, tag, publish, or update website worktrees.
- If the touched area includes bootstrap, startup, or public claims, finish with the manual watchpoints in `docs/client-parity-checklist.md`.
