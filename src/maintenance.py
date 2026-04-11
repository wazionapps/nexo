"""Placeholder module for historical `maintenance` runner.

HISTORICAL CONTEXT (NEXO-AUDIT-2026-04-11 finding, Learning #194)
==================================================================

This module previously exposed `check_and_run_overdue()` and a private
`_run_task()` dispatcher that walked the `maintenance_schedule` table and
executed cognitive decay, synthesis, self audit, weight learning, somatic
decay, somatic projection, drive decay and graph maintenance as needed.

None of that code was ever called from anywhere. A repository-wide grep
for `check_and_run_overdue`, `from maintenance`, `import maintenance` and
`maintenance.check` produced zero hits during the 2026-04-11 audit. Each
of those tasks actually runs from its own LaunchAgent via its own script
in `src/scripts/` (e.g. `nexo-cognitive-decay.py`, `nexo-daily-self-audit
.py`, `nexo-evolution-run.py`), not through this dispatcher. The
`maintenance_schedule` table in SQLite is still populated by migrations
(see `db/_schema.py::_m9_maintenance_schedule` and the drive_decay
registration) but is effectively dead data: nothing reads it.

Why the dispatcher was removed
------------------------------
The dead dispatcher was actively misleading: developers reading the code
could reasonably conclude that adding a row to `maintenance_schedule`
would cause the named task to run. It would not. That false contract
was the root of a near-miss during the Item 9 fix of the 2026-04-11
audit, where the first plan was to register a new `read_token_cleanup`
task via this mechanism before discovering that the mechanism is never
invoked. See `src/db/_reminders.py::_purge_expired_read_tokens_if_due`
for the opportunistic-cleanup pattern that replaced that initial plan.

What to do if you need scheduled maintenance
--------------------------------------------
Do NOT reintroduce a dispatcher in this module. Pick one of:

  * Add your work to an existing LaunchAgent script under
    `src/scripts/` that already runs on the cadence you need.
  * Register a new personal script via `nexo_personal_script_create` and
    let the schedule/LaunchAgent system handle it.
  * Run the cleanup opportunistically inside the hot path, throttled
    by wall-clock (see `_purge_expired_read_tokens_if_due`).

What happens to the `maintenance_schedule` table
-------------------------------------------------
The table is intentionally left in place. Removing it would require a
destructive migration for every installed user with no benefit — the
rows do no harm, cost a few KB each, and their removal is deferred to a
future cleanup pass when migration numbering is renegotiated.
"""

from __future__ import annotations

__all__: list[str] = []
