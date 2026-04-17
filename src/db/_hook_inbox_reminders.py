"""NEXO DB — Hook inbox reminder bookkeeping (v6.0.1).

The ``PostToolUse`` hook may surface a ``systemMessage`` that tells the
agent it has unread ``nexo_send`` messages when the session has been
autopiloting through tool calls for a while. This module backs the rate
limit: at most one reminder per minute per SID, stored in the tiny
``hook_inbox_reminders`` table created by migration m42.

All helpers are best-effort on the read path and raise on unexpected
write failures — callers (the hook itself) wrap calls in try/except so
a malformed DB never breaks the tool pipeline.
"""
from __future__ import annotations

from db._core import get_db


def get_last_reminder_ts(sid: str) -> float | None:
    """Return the epoch seconds of the last inbox reminder for ``sid``.

    Returns None when no row exists yet. Never raises — treats any
    unexpected error as "no prior reminder recorded" so the hook can
    decide to emit a fresh one.
    """
    if not sid:
        return None
    try:
        row = get_db().execute(
            "SELECT last_reminder_ts FROM hook_inbox_reminders WHERE sid = ?",
            (sid,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    try:
        return float(row[0]) if row[0] is not None else None
    except (TypeError, ValueError):
        return None


def mark_reminder_sent(sid: str, ts: float) -> None:
    """Record that a reminder was surfaced for ``sid`` at ``ts``.

    Uses SQLite UPSERT so the table tracks one row per SID. Silently
    swallows DB errors; the hook caller logs / skips as needed.
    """
    if not sid:
        return
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO hook_inbox_reminders (sid, last_reminder_ts) "
            "VALUES (?, ?) "
            "ON CONFLICT(sid) DO UPDATE SET last_reminder_ts = excluded.last_reminder_ts",
            (sid, float(ts)),
        )
        conn.commit()
    except Exception:
        pass


def reset_reminders_for_sid(sid: str) -> None:
    """Delete the reminder row for ``sid``. Used by tests that want to
    start from a clean slate between assertions."""
    if not sid:
        return
    try:
        conn = get_db()
        conn.execute("DELETE FROM hook_inbox_reminders WHERE sid = ?", (sid,))
        conn.commit()
    except Exception:
        pass
