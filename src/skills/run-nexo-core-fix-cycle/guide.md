# Run NEXO Core Fix Cycle

Use this skill when a small group of NEXO core fixes must be implemented and verified without improvising the plane or repeating the same discovery and test phase every time.

## Steps
1. First fix the plane: public `nexo` repo, installed `~/.nexo` runtime, and public claims/docs. Do not mix those three worlds.
2. Open `nexo_task_open(...)` and `nexo_workflow_open(...)` before touching code. If the fix is action-related, also pass through `nexo_cortex_decide(...)`.
3. Run the skill with `areas` adjusted to the fix. The helper returns the file map and runs the focused test battery for `protocol`, `plane`, `guard`, `cortex`, and/or `release`.
4. Implement the smallest defensible change only on the correct surface. If the problem is product-level, fix it in the repo, not in `~/.nexo`.
5. Rerun the skill to revalidate the exact test cluster touched by the fix.
6. Close with `nexo_task_close(...)` and real evidence. If there was a real edit, leave `change_log` and capture a learning if a canonical rule changed.

## Gotchas
- Do not use diary, workflow text, or intuition as a substitute for real git/tests/runtime evidence.
- If the fix touches doctor or public claims, set the explicit `plane` before running diagnostics.
- If the fix touches release or runtime update, use the official path (`nexo update`, doctor, final release skill), not side scripts.
- If the helper does not find an area, add the new surface explicitly to the skill instead of continuing scattered manual grep.
