NEXO PROTOCOL (MANDATORY):
- Before non-trivial analyze/edit/execute/delegate work, call `nexo_task_open(...)`. If that tool is unavailable, call `nexo_guard_check(...)` and `nexo_cortex_check(...)` first.
- For long multi-step or cross-session work, call `nexo_workflow_open(...)` and keep it updated with `nexo_workflow_update(...)` so resume/replay use durable state instead of guesswork.
- Before diagnosing NEXO, explicitly fix the plane first: `product_public`, `runtime_personal`, `installation_live`, `database_real`, or `cooperator`. Do not mix planes inside the same diagnosis.
- If a target file has conditioned learnings or blocking guard rules, review them before any read/edit/delete step, and acknowledge guard before any edit/delete step.
- Do not claim done without explicit verification evidence. Close with `nexo_task_close(...)`; if unavailable, capture the change log and state the evidence explicitly.
- When a correction changes the canonical rule, capture or supersede the learning instead of leaving contradictory active rules behind.
