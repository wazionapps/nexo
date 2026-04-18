"""Tests for reminder/followup history-aware CRUD handlers."""

from __future__ import annotations

import tools_reminders_crud as reminders_tools


def _extract_read_token(payload: str) -> str:
    for line in payload.splitlines():
        if line.startswith("READ_TOKEN: "):
            return line.split("READ_TOKEN: ", 1)[1].strip()
    raise AssertionError(f"READ_TOKEN not found in payload:\n{payload}")


def test_reminder_handlers_require_read_before_mutation(isolated_db):
    created = reminders_tools.handle_reminder_create(
        id="R-HIST-1",
        description="History aware reminder",
        date="2026-04-09",
        category="tasks",
    )
    assert "Reminder created." in created

    denied = reminders_tools.handle_reminder_update(
        id="R-HIST-1",
        description="Updated without read token",
    )
    assert "Missing read_token" in denied

    detail = reminders_tools.handle_reminder_get("R-HIST-1")
    token = _extract_read_token(detail)
    assert "Usage rules:" in detail
    assert "History:" in detail

    updated = reminders_tools.handle_reminder_update(
        id="R-HIST-1",
        description="Updated with read token",
        read_token=token,
    )
    assert "updated" in updated

    stale = reminders_tools.handle_reminder_delete(id="R-HIST-1", read_token=token)
    assert "History changed" in stale

    refreshed = reminders_tools.handle_reminder_get("R-HIST-1")
    fresh_token = _extract_read_token(refreshed)
    noted = reminders_tools.handle_reminder_note(
        id="R-HIST-1",
        note="Asked Alice and waiting for reply.",
        read_token=fresh_token,
    )
    assert "note added" in noted


def test_followup_handlers_soft_delete_and_restore(isolated_db):
    created = reminders_tools.handle_followup_create(
        id="NF-HIST-1",
        description="History aware followup",
        date="2026-04-09",
        verification="Check the logs",
        reasoning="Regression coverage",
        priority="high",
    )
    assert "Followup created." in created
    assert "Priority: high." in created

    detail = reminders_tools.handle_followup_get("NF-HIST-1")
    token = _extract_read_token(detail)
    assert "Usage rules:" in detail
    assert "History:" in detail
    assert "Priority: high" in detail

    deleted = reminders_tools.handle_followup_delete(id="NF-HIST-1", read_token=token)
    assert "soft-deleted" in deleted

    deleted_detail = reminders_tools.handle_followup_get("NF-HIST-1")
    assert "Status: DELETED" in deleted_detail
    restore_token = _extract_read_token(deleted_detail)

    restored = reminders_tools.handle_followup_restore(id="NF-HIST-1", read_token=restore_token)
    assert "restored to PENDING" in restored

    noted_detail = reminders_tools.handle_followup_get("NF-HIST-1")
    note_token = _extract_read_token(noted_detail)
    noted = reminders_tools.handle_followup_note(
        id="NF-HIST-1",
        note="Alice answered, execute the task and close.",
        read_token=note_token,
    )
    assert "note added" in noted
