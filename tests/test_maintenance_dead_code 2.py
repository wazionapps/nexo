"""Regression test for the maintenance.py dead-code removal.

Records the invariant that src/maintenance.py must NOT re-export the old
`check_and_run_overdue` / `_run_task` dispatcher that was removed in the
NEXO-AUDIT-2026-04-11 cleanup (Learning #194). If a future change adds
those names back, this test fails loudly so whoever is doing it has to
either rename or confirm the intent.

Why a test and not just a comment? During the audit we confirmed that
the dispatcher had been dead code from the moment it was written, and
that the existence of the false contract almost caused a regression.
A test is the only way to keep that lesson compiled.
"""

from __future__ import annotations

import importlib


def test_maintenance_module_does_not_re_export_dead_dispatcher():
    mod = importlib.import_module("maintenance")

    # The module is now a placeholder. It must not carry over the removed
    # dispatcher surface. If you are here because you want to add one of
    # these names back, STOP and read the docstring of src/maintenance.py
    # first — the correct answer is almost certainly to attach your work
    # to an existing LaunchAgent script, not to revive this dispatcher.
    forbidden = [
        "check_and_run_overdue",
        "_run_task",
    ]
    for name in forbidden:
        assert not hasattr(mod, name), (
            f"maintenance.{name} must not be re-exported. "
            "See NEXO-AUDIT-2026-04-11 Learning #194 and the docstring of "
            "src/maintenance.py for the rationale."
        )


def test_maintenance_module_has_empty_public_surface():
    mod = importlib.import_module("maintenance")

    # The module is a placeholder with no public API.
    assert getattr(mod, "__all__", None) == [], (
        "maintenance.__all__ must stay empty — the module is a historical "
        "placeholder. See src/maintenance.py docstring."
    )
