# Run Runtime Doctor

Use this skill when you want a fast health snapshot of the running NEXO system.

## Steps
1. Run the runtime doctor for the requested tier.
2. Review the degraded or critical checks first.
3. If the report recommends deterministic fixes, decide whether to run them explicitly.

## Gotchas
- A critical watchdog result reflects a real system issue, not just a stale skill.
- `all` is broader and slower than `runtime`.
